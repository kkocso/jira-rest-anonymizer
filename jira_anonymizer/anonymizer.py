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


def _is_user_role_key(key: Optional[str]) -> bool:
    return key in USER_ROLE_KEYS


def _is_activity_list_key(key: Optional[str]) -> bool:
    return key in ACTIVITY_LIST_KEYS


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
        self._activity_start = self._parse_timestamp(config.activity_start_timestamp)
        self._activity_end = self._parse_timestamp(config.activity_end_timestamp)

    def anonymize(self, data: Any) -> Any:
        """
        Return an anonymized deep copy of `data`.
        """
        return self._walk(deepcopy(data))

    # Internal helpers -----------------------------------------------------

    def _walk(self, node: Any, parent_key: str | None = None) -> Any:
        if isinstance(node, dict):
            return self._walk_dict(node, parent_key=parent_key)
        if isinstance(node, list):
            # Optionally filter activity-style lists (worklogs, changelogs, etc.)
            filtered = self._maybe_filter_activity_list(node, parent_key=parent_key)
            return [self._walk(item, parent_key=parent_key) for item in filtered]
        return node

    def _walk_dict(self, obj: Dict[str, Any], parent_key: str | None = None) -> Dict[str, Any]:
        # Special handling for JIRA user objects.
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
        # Heuristic: typical JIRA user object keys.
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
            # If we have an anonymized email, derive displayName from its local part
            # so the connection between displayName and emailAddress is obvious.
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

        # Use the already-synced "self" URL as-is (do not pass through _walk),
        # so it is never run through _anonymize_url and stays in sync with accountId.
        result: Dict[str, Any] = {}
        for k, v in user.items():
            if k == "self":
                result[k] = user["self"]
            else:
                result[k] = self._walk(v, parent_key=k)
        return result

    # Activity filtering (worklog, changelog, etc.) ------------------------

    def _parse_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    def _within_activity_window(self, ts_str: str) -> bool:
        # If no window configured, keep everything.
        if self._activity_start is None and self._activity_end is None:
            return True

        ts = self._parse_timestamp(ts_str)
        if ts is None:
            # If we can't parse, keep the entry rather than dropping it silently.
            return True

        if self._activity_start is not None and ts < self._activity_start:
            return False
        if self._activity_end is not None and ts > self._activity_end:
            return False
        return True

    def _maybe_filter_activity_list(
        self, items: Iterable[Any], parent_key: Optional[str]
    ) -> Iterable[Any]:
        if not _is_activity_list_key(parent_key):
            return items

        filtered: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                filtered.append(item)
                continue
            ts_str = item.get("created") or item.get("started")
            # If there is no timestamp, keep the item unconditionally.
            if not isinstance(ts_str, str):
                filtered.append(item)
                continue
            # Only filter out items when we have a valid, parseable timestamp.
            if self._within_activity_window(ts_str):
                filtered.append(item)
        return filtered

    def _anonymize_rich_text(self, text: str) -> str:
        """
        Anonymize long, free-form text (e.g. description, comments) by:
        - Replacing issue keys.
        - Replacing embedded JIRA accountId references.
        - Replacing email addresses.
        - Replacing most standalone numbers (while preserving formatting markers like h2).
        - Replacing likely company names (sequences of capitalized words).
        """

        # 0) Issue keys like QAE-26 appearing in free text.
        issue_key_pattern = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")

        def _issue_key_replace(match: re.Match[str]) -> str:
            key = match.group(0)
            return self._anonymize_issue_key(key)

        text = issue_key_pattern.sub(_issue_key_replace, text)

        # 1) Inline JIRA accountId references like [~accountid:5b9b6ae23e56f62be]
        accountid_pattern = re.compile(r"\[~accountid:([^\]]+)\]")

        def _accountid_replace(match: re.Match[str]) -> str:
            acct = match.group(1)
            anon = self._mappings.user_id(acct)
            return f"[~accountid:{anon}]"

        text = accountid_pattern.sub(_accountid_replace, text)

        # 2) Emails
        email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

        def _email_replace(match: re.Match[str]) -> str:
            addr = match.group(0)
            return self._mappings.email(addr)

        text = email_pattern.sub(_email_replace, text)

        # 3) Company-like names: sequences of 2+ capitalized words.
        company_pattern = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)\b")

        def _company_replace(match: re.Match[str]) -> str:
            name = match.group(0)
            # Map to a generic value-based pseudonym.
            return self._mappings.string(name)

        text = company_pattern.sub(_company_replace, text)

        # 4) Numbers: replace standalone numbers, but keep things like "h2"
        # and avoid re-anonymizing numbers that are already part of
        # anonymized issue keys (e.g. JIRAPROJ_001-00179).
        number_pattern = re.compile(r"\b\d+\b")

        def _number_replace(match: re.Match[str]) -> str:
            start = match.start()
            digits = match.group(0)
            # Preserve formatting markers like "h2", "h3", etc.
            if start > 0:
                prev_char = text[start - 1]
                if prev_char in {"h", "H"} and digits in {"1", "2", "3", "4", "5", "6"}:
                    return digits
                # If this number is immediately preceded by a dash that is
                # itself preceded by a valid JIRA project key prefix
                # (uppercase letters/digits), treat it as part of an issue key
                # and leave it unchanged.
                if prev_char == "-":
                    i = start - 2
                    while i >= 0 and (text[i].isalnum() or text[i] == "_"):
                        i -= 1
                    # Extract the candidate prefix.
                    if i < start - 2:
                        prefix = text[i + 1 : start - 1]
                        # Allow underscores so anonymized project keys like
                        # JIRAPROJ_001 are treated as valid prefixes and their
                        # numeric parts are not re-anonymized.
                        if re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix):
                            return digits
            return self._mappings.number(digits, len(digits))

        text = number_pattern.sub(_number_replace, text)

        return text

    # Primitive fields -----------------------------------------------------

    def _anonymize_primitive_field(self, key: str, value: str, parent_key: str | None = None) -> str:
        # Description-like long text fields: strip embedded sensitive data.
        if key in RICH_TEXT_KEYS:
            return self._anonymize_rich_text(value)

        # Changelog value fields
        if key in CHANGELOG_NUMERIC_KEYS:
            # Issue keys like QAE-26
            if re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", value):
                return self._anonymize_issue_key(value)
            # Plain numeric IDs (e.g. sprint IDs like 9884)
            if value.isdigit():
                return self._mappings.number(value, len(value))

        if key in CHANGELOG_TEXT_KEYS:
            # Treat as rich text wherever it appears: anonymize issue keys,
            # emails, numbers, company names, etc.
            return self._anonymize_rich_text(value)

        if key == "fieldId" and value.startswith("customfield_"):
            # Mirror customfield key renaming for changelog fieldId values.
            return self._mappings.customfield_key(value)

        # Emails
        if self._config.anonymize_emails and key == "emailAddress":
            return self._mappings.email(value)

        # User identifiers
        if self._config.anonymize_account_ids and key in {"accountId", "name"}:
            return self._mappings.user_id(value)

        if key == "displayName" and self._config.anonymize_display_names:
            anon_id = self._mappings.user_id(value)
            return f"User {anon_id.split('_')[-1]}"

        # Issue keys (e.g. "ONEANDROID-1179")
        if key == "key":
            return self._anonymize_issue_key(value)

        # Issue numeric IDs (e.g. 10001) usually next to "key"
        if key == "id" and value.isdigit():
            return self._mappings.number(value, len(value))

        # Worklog identifiers
        if key == "worklogId" and value.isdigit():
            return self._mappings.number(value, len(value))

        # URLs
        # Skip when the object containing this "self" was reached via a user-role key
        # (parent_key is that key). User objects are handled in _anonymize_user_object,
        # which keeps "self" in sync with accountId and does not pass it through here.
        if (
            self._config.anonymize_urls
            and key == "self"
            and not _is_user_role_key(parent_key)
        ):
            return self._anonymize_url(value)

        # Avatar URLs under avatarUrls objects
        if parent_key == AVATAR_URLS_KEY:
            return self._anonymize_avatar_url(value)

        # Customfield values (if configured)
        if self._config.anonymize_customfield_values and key.startswith("customfield_"):
            return self._mappings.string(value)

        return value

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

