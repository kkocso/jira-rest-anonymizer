from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any, Dict, Mapping, Iterable, Optional

from .config import AnonymizerConfig
from .mapping_store import MappingStore


# Common JIRA-related key constants and helpers -----------------------------

RICH_TEXT_KEYS = {"description", "comment", "body"}
CHANGELOG_NUMERIC_KEYS = {"from", "to"}
CHANGELOG_TEXT_KEYS = {"fromString", "toString"}
ACTIVITY_LIST_KEYS = {"worklog", "worklogs", "changelog", "histories", "comments", "comment"}
USER_ROLE_KEYS = {"reporter", "assignee", "creator", "author", "updateAuthor", "actor", "user"}
ATTACHMENT_KEYS = {"attachment", "attachments"}
AVATAR_URLS_KEY = "avatarUrls"

# Rich-text regex patterns (compiled once at module load)
_RE_ISSUE_KEY_IN_TEXT = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
_RE_ACCOUNTID_INLINE = re.compile(r"\[~accountid:([^\]]+)\]")
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_COMPANY_LIKE = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)\b")
_RE_STANDALONE_NUMBER = re.compile(r"\b\d+\b")
_RE_ISSUE_KEY_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*")


def _is_user_role_key(key: Optional[str]) -> bool:
    return key in USER_ROLE_KEYS


def _is_activity_list_key(key: Optional[str]) -> bool:
    return key in ACTIVITY_LIST_KEYS


# Activity window filtering -------------------------------------------------

def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


class ActivityWindowFilter:
    """
    Filters activity-style list items (worklogs, changelog entries, etc.) by
    an optional inclusive timestamp window [start, end].
    """

    def __init__(
        self,
        start_timestamp: Optional[str],
        end_timestamp: Optional[str],
    ) -> None:
        self._start = _parse_timestamp(start_timestamp)
        self._end = _parse_timestamp(end_timestamp)

    def keep_timestamp(self, ts_str: str) -> bool:
        """Return True if the timestamp is inside the window (or no window is set)."""
        if self._start is None and self._end is None:
            return True
        ts = _parse_timestamp(ts_str)
        if ts is None:
            return True
        if self._start is not None and ts < self._start:
            return False
        if self._end is not None and ts > self._end:
            return False
        return True

    def keep_activity_item(self, item: Any) -> bool:
        """Return True if the item should be kept (e.g. worklog or changelog entry)."""
        if not isinstance(item, dict):
            return True
        ts_str = item.get("created") or item.get("started")
        if not isinstance(ts_str, str):
            return True
        return self.keep_timestamp(ts_str)


class Anonymizer:
    """
    Simple structure-preserving anonymizer for common JIRA REST responses.

    It focuses on:
    - User objects (`reporter`, `assignee`, etc.)
    - `emailAddress`, `accountId`, `displayName`, `name`
    - `self` links and other URLs
    - `customfield_*` keys (optionally renamed via a map)
    """

    def __init__(
        self,
        config: AnonymizerConfig,
        customfield_map: Mapping[str, str] | None,
        mapping_store: MappingStore,
    ) -> None:
        self._config = config
        self._customfield_map = dict(customfield_map or {})
        self._mappings = mapping_store
        self._activity_filter = ActivityWindowFilter(
            config.activity_start_timestamp,
            config.activity_end_timestamp,
        )

    def anonymize(self, data: Any) -> Any:
        """
        Return an anonymized deep copy of `data`.
        """
        return self._walk(deepcopy(data))

    def _walk(self, node: Any, parent_key: str | None = None) -> Any:
        """
        Recursively traverse the JIRA JSON tree, keeping the original structure
        but routing nodes into the appropriate anonymization helpers.
        """
        if isinstance(node, dict):
            return self._walk_dict(node, parent_key=parent_key)
        if isinstance(node, list):
            # Optionally filter activity-style lists (worklogs, changelogs, etc.)
            filtered = self._maybe_filter_activity_list(node, parent_key=parent_key)
            return [self._walk(item, parent_key=parent_key) for item in filtered]
        return node

    def _walk_dict(self, obj: Dict[str, Any], parent_key: str | None = None) -> Dict[str, Any]:
        """
        Traverse a single object node, handling structural concerns:
        - Detect and delegate real JIRA user dictionaries.
        - Drop attachments.
        - Rename customfield keys.
        - Delegate scalar anonymization to _anonymize_primitive_field.
        - Recurse into nested dicts/lists via _walk.
        """
        # Special handling for JIRA user objects, delegated to a dedicated helper.
        if self._config.anonymize_users and self._looks_like_user(obj):
            return self._anonymize_user_object(obj)

        new_obj: Dict[str, Any] = {}

        for key, value in obj.items():
            # Drop attachments entirely from the output.
            if key in ATTACHMENT_KEYS:
                continue

            # Handle customfield renames at key level.
            new_key = self._rename_customfield_key(key)

            # Primitive field replacements based on key.
            if isinstance(value, str):
                new_obj[new_key] = self._anonymize_primitive_field(new_key, value, parent_key=parent_key)
            else:
                # Recurse if complex; we pass the key for context if needed later.
                new_obj[new_key] = self._walk(value, parent_key=new_key)

        return new_obj

    # User handling --------------------------------------------------------

    @staticmethod
    def _looks_like_user(obj: Mapping[str, Any]) -> bool:
        """
        Heuristic for recognizing a JIRA user dictionary based on its keys.
        Traversal code uses this to decide when to hand off to
        _anonymize_user_object instead of treating the mapping as a generic
        dictionary.
        """
        user_keys = {"accountId", "emailAddress", "displayName"}
        return any(k in obj for k in user_keys)

    def _anonymize_user_object(self, user: Dict[str, Any]) -> Dict[str, Any]:
        account_id = user.get("accountId")
        if isinstance(account_id, str) and self._config.anonymize_account_ids:
            anon_account_id = self._mappings.user_id(account_id)
            user["accountId"] = anon_account_id

            # Keep `self` URLs that embed the accountId in sync with the anonymized value.
            self_url = user.get("self")
            if isinstance(self_url, str) and account_id in self_url:
                user["self"] = self_url.replace(account_id, anon_account_id)

        anon_email_local: str | None = None
        if self._config.anonymize_emails:
            email = user.get("emailAddress")
            if isinstance(email, str):
                anon_email = self._mappings.email(email)
                user["emailAddress"] = anon_email
                anon_email_local = anon_email.split("@", 1)[0]

        display_name = user.get("displayName")
        if self._config.anonymize_display_names and isinstance(display_name, str):
            # When an email address is present, derive displayName from the same
            # underlying anonymized identifier so you can immediately see which
            # display name belongs to which email. Otherwise, fall back to a
            # deterministic mapping based on the displayName itself.
            if anon_email_local:
                suffix = anon_email_local.split("_")[-1] if "_" in anon_email_local else anon_email_local
                user["displayName"] = f"User {suffix}"
            else:
                anon_id = self._mappings.user_id(display_name)
                user["displayName"] = f"User {anon_id.split('_')[-1]}"

        name = user.get("name")
        if isinstance(name, str) and self._config.anonymize_account_ids:
            anon_id = self._mappings.user_id(name)
            user["name"] = anon_id

        # Decide how to handle the self URL:
        # - When account IDs are anonymized, we keep `self` in sync with the
        #   anonymized accountId and do not run it through URL anonymization.
        # - When account IDs are left as-is but URL anonymization is enabled,
        #   we still anonymize numeric identifiers inside the URL like any
        #   other URL in the payload.
        anon_self: str | None = None
        self_url = user.get("self")
        if isinstance(self_url, str) and not self._config.anonymize_account_ids and self._config.anonymize_urls:
            anon_self = self._anonymize_url(self_url)

        result: Dict[str, Any] = {}
        for k, v in user.items():
            if k == "self":
                if anon_self is not None:
                    result[k] = anon_self
                else:
                    result[k] = self_url
            else:
                result[k] = self._walk(v, parent_key=k)
        return result

    # Activity filtering (worklog, changelog, etc.) ------------------------

    def _maybe_filter_activity_list(
        self, items: Iterable[Any], parent_key: Optional[str]
    ) -> Iterable[Any]:
        if not _is_activity_list_key(parent_key):
            return items
        return [item for item in items if self._activity_filter.keep_activity_item(item)]

    def _anonymize_rich_text(self, text: str) -> str:
        """
        Anonymize long, free-form text (e.g. description, comments) by
        applying issue-key, accountId, email, company-name, and number
        replacements in sequence.
        """
        text = self._replace_issue_keys_in_text(text)
        if self._config.anonymize_account_ids:
            text = self._replace_inline_account_ids_in_text(text)
        if self._config.anonymize_emails:
            text = self._replace_emails_in_text(text)
        text = self._replace_company_names_in_text(text)
        text = self._replace_numbers_in_text(text)
        return text

    def _replace_issue_keys_in_text(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            return self._anonymize_issue_key(match.group(0))
        return _RE_ISSUE_KEY_IN_TEXT.sub(_repl, text)

    def _replace_inline_account_ids_in_text(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            anon = self._mappings.user_id(match.group(1))
            return f"[~accountid:{anon}]"
        return _RE_ACCOUNTID_INLINE.sub(_repl, text)

    def _replace_emails_in_text(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            return self._mappings.email(match.group(0))
        return _RE_EMAIL.sub(_repl, text)

    def _replace_company_names_in_text(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            return self._mappings.string(match.group(0))
        return _RE_COMPANY_LIKE.sub(_repl, text)

    def _replace_numbers_in_text(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            start = match.start()
            digits = match.group(0)
            if start > 0:
                prev_char = text[start - 1]
                if prev_char in {"h", "H"} and digits in {"1", "2", "3", "4", "5", "6"}:
                    return digits
                if prev_char == "-":
                    i = start - 2
                    while i >= 0 and (text[i].isalnum() or text[i] == "_"):
                        i -= 1
                    if i < start - 2:
                        prefix = text[i + 1 : start - 1]
                        if _RE_ISSUE_KEY_PREFIX.fullmatch(prefix):
                            return digits
            return self._mappings.number(digits, len(digits))
        return _RE_STANDALONE_NUMBER.sub(_repl, text)

    # Primitive fields -----------------------------------------------------

    def _anonymize_primitive_field(self, key: str, value: str, parent_key: str | None = None) -> str:
        if key in RICH_TEXT_KEYS:
            return self._anonymize_rich_text(value)

        result = self._anonymize_changelog_field(key, value)
        if result is not None:
            return result
        result = self._anonymize_user_related_field(key, value)
        if result is not None:
            return result
        result = self._anonymize_id_field(key, value)
        if result is not None:
            return result
        result = self._anonymize_url_or_avatar_field(key, value, parent_key)
        if result is not None:
            return result
        result = self._anonymize_customfield_value_field(key, value)
        if result is not None:
            return result
        return value

    def _anonymize_changelog_field(self, key: str, value: str) -> Optional[str]:
        if key in CHANGELOG_NUMERIC_KEYS:
            if re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", value):
                return self._anonymize_issue_key(value)
            if value.isdigit():
                return self._mappings.number(value, len(value))
        if key in CHANGELOG_TEXT_KEYS:
            return self._anonymize_rich_text(value)
        if key == "fieldId" and value.startswith("customfield_"):
            return self._rename_customfield_key(value)
        return None

    def _anonymize_user_related_field(self, key: str, value: str) -> Optional[str]:
        if self._config.anonymize_emails and key == "emailAddress":
            return self._mappings.email(value)
        if self._config.anonymize_account_ids and key in {"accountId", "name"}:
            return self._mappings.user_id(value)
        if key == "displayName" and self._config.anonymize_display_names:
            anon_id = self._mappings.user_id(value)
            return f"User {anon_id.split('_')[-1]}"
        return None

    def _anonymize_id_field(self, key: str, value: str) -> Optional[str]:
        if key == "key":
            return self._anonymize_issue_key(value)
        if key == "id" and value.isdigit():
            return self._mappings.number(value, len(value))
        if key == "worklogId" and value.isdigit():
            return self._mappings.number(value, len(value))
        return None

    def _anonymize_url_or_avatar_field(
        self, key: str, value: str, parent_key: str | None
    ) -> Optional[str]:
        if (
            self._config.anonymize_urls
            and key == "self"
            and not _is_user_role_key(parent_key)
        ):
            return self._anonymize_url(value)
        if parent_key == AVATAR_URLS_KEY:
            return self._anonymize_avatar_url(value)
        return None

    def _anonymize_customfield_value_field(self, key: str, value: str) -> Optional[str]:
        if self._config.anonymize_customfield_values and key.startswith("customfield_"):
            return self._mappings.string(value)
        return None

    def _anonymize_issue_key(self, value: str) -> str:
        """
        Anonymize JIRA issue keys of the form "PROJECT-1234".

        - The project prefix (e.g. "ONEANDROID") is replaced with a stable,
          fake project code via MappingStore.project_key (e.g. "JIRAPROJ_001").
        - The numeric part (e.g. "1179") is replaced with a stable, fake
          number via MappingStore.number, preserving length.
        """
        match = re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", value)
        if not match:
            return value

        project, number = match.groups()
        anon_project = self._mappings.project_key(project)
        anon_number = self._mappings.number(number, len(number))
        return f"{anon_project}-{anon_number}"

    def _anonymize_url(self, value: str) -> str:
        """
        Anonymize URLs by randomizing numeric identifiers inside them while
        keeping the general structure readable.

        Each distinct digit sequence is replaced with a stable, unique
        pseudo-random number of the same length.
        """

        def _replace(match: re.Match[str]) -> str:
            digits = match.group(0)
            return self._mappings.number(digits, len(digits))

        return re.sub(r"\d+", _replace, value)

    def _anonymize_avatar_url(self, value: str) -> str:
        """
        Anonymize avatar URLs by replacing the long avatar token segment
        with a deterministic, same-length pseudonym, preserving dashes.
        """

        # Match one or more hex segments separated by dashes, with no fixed
        # requirements on segment length or count (e.g. "abc-def-1234-56").
        pattern = re.compile(r"[0-9a-fA-F]+(?:-[0-9a-fA-F]+)+")

        def _replace(match: re.Match[str]) -> str:
            token = match.group(0)
            return self._mappings.avatar_token(token)

        return pattern.sub(_replace, value)

    # Customfield key renaming ---------------------------------------------

    def _rename_customfield_key(self, key: str) -> str:
        if not key.startswith("customfield_"):
            return key

        # First allow explicit mappings.
        if key in self._customfield_map:
            return self._customfield_map[key]

        # Otherwise use a deterministic pseudonym so that:
        # - Every original customfield key gets a unique replacement.
        # - The same original key always maps to the same pseudonym.
        return self._mappings.customfield_key(key)

