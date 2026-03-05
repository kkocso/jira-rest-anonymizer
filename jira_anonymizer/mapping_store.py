from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


_MAPPING_FIELDS = (
    "users",
    "emails",
    "urls",
    "strings",
    "customfields",
    "numbers",
    "project_keys",
    "avatars",
    "hosts",
)


@dataclass
class MappingStore:
    """
    Keeps deterministic mappings for different types of sensitive values.

    The same input value will always get the same anonymized value,
    and mappings can be persisted to disk between runs.
    """

    users: Dict[str, str] = field(default_factory=dict)
    emails: Dict[str, str] = field(default_factory=dict)
    urls: Dict[str, str] = field(default_factory=dict)
    strings: Dict[str, str] = field(default_factory=dict)
    customfields: Dict[str, str] = field(default_factory=dict)
    numbers: Dict[str, str] = field(default_factory=dict)
    project_keys: Dict[str, str] = field(default_factory=dict)
    avatars: Dict[str, str] = field(default_factory=dict)
    hosts: Dict[str, str] = field(default_factory=dict)

    def _next_id(self, prefix: str, existing: Dict[str, str]) -> str:
        current_ids = [
            int(v.split("_")[-1])
            for v in existing.values()
            if v.startswith(prefix + "_") and v.split("_")[-1].isdigit()
        ]
        next_index = max(current_ids, default=0) + 1
        return f"{prefix}_{next_index:03d}"

    def _get_or_create(self, value: str, store: Dict[str, str], prefix: str) -> str:
        if value in store:
            return store[value]
        anonymized = self._next_id(prefix, store)
        store[value] = anonymized
        return anonymized

    def user_id(self, value: str) -> str:
        return self._get_or_create(value, self.users, "user")

    def _merge_user_id_into(self, target_id: str, source_id: str) -> None:
        """
        Reassign every key in self.users that currently maps to source_id
        to target_id, so one canonical id wins and displayName/email stay consistent.
        """
        if source_id == target_id:
            return
        for k, v in list(self.users.items()):
            if v == source_id:
                self.users[k] = target_id

    def register_person_aliases(
        self,
        account_id: str | None,
        email: str | None,
        display_name: str | None,
        name: str | None,
    ) -> None:
        """
        Register that accountId, email, displayName, and name belong to the same
        person so they all get the same anonymized user id everywhere.
        When multiple identifiers already map to different ids (e.g. from earlier
        runs or other files), they are merged into one canonical id so displayName
        and email stay consistent across all occurrences.
        """
        identifiers = [
            (account_id, "accountId"),
            (email, "email"),
            (display_name, "displayName"),
            (name, "name"),
        ]
        present = [(v, kind) for v, kind in identifiers if v]
        if not present:
            return

        # Prefer accountId > email > displayName > name as canonical so we merge to a stable id.
        canonical_value = (
            account_id
            or email
            or display_name
            or name
        )
        canonical_id = self.user_id(canonical_value)

        for value, _ in present:
            if value == canonical_value:
                continue
            existing_id = self.users.get(value)
            if existing_id is not None and existing_id != canonical_id:
                self._merge_user_id_into(canonical_id, existing_id)
            self.users[value] = canonical_id

    def email(self, value: str) -> str:
        # Use the same user id as for accountId/displayName so one person = one id.
        local = self.user_id(value)
        return f"{local}@example.invalid"

    def url(self, value: str) -> str:
        return self._get_or_create(value, self.urls, "url")

    def string(self, value: str) -> str:
        return self._get_or_create(value, self.strings, "value")

    def customfield_key(self, value: str) -> str:
        """
        Generate a unique, stable pseudonym for a customfield key.

        This keeps the mapping deterministic across runs while ensuring
        that each original customfield key gets a unique replacement.
        """
        return self._get_or_create(value, self.customfields, "customfield")

    def number(self, value: str, length: int) -> str:
        """
        Generate a unique, stable numeric replacement for a digit sequence.

        The same original digit sequence will always map to the same
        replacement, and different sequences get different replacements.
        The replacement preserves the original length by zero-padding
        as long as there is still unused space for that length; if the
        numeric space is exhausted, the anonymized value may grow in
        length to avoid collisions.
        """
        if value in self.numbers:
            return self.numbers[value]

        used_values = set(self.numbers.values())
        current_ids = [int(v) for v in used_values if v.isdigit()]
        next_index = max(current_ids, default=0) + 1

        # Find the next unused numeric string, zero-padded to `length` where possible.
        while True:
            anonymized = str(next_index).zfill(length)
            if anonymized not in used_values:
                break
            next_index += 1

        self.numbers[value] = anonymized
        return anonymized

    def project_key(self, value: str) -> str:
        """
        Generate a unique, stable replacement for a JIRA project key prefix.

        For example, multiple real project keys like "ONEANDROID" and "BACKEND"
        might become "JIRAPROJ_001", "JIRAPROJ_002", etc.
        """
        return self._get_or_create(value, self.project_keys, "JIRAPROJ")

    def host(self, value: str) -> str:
        """
        Generate a unique, stable replacement for a URL hostname (e.g. JIRA domain).

        For example, "accedobroadband.jira.com" might become "jira-001.example.invalid".
        """
        if value in self.hosts:
            return self.hosts[value]
        current = [
            int(m.group(1))
            for v in self.hosts.values()
            for m in [re.search(r"^jira-(\d+)\.example\.invalid$", v)]
            if m
        ]
        next_index = max(current, default=0) + 1
        anonymized = f"jira-{next_index:03d}.example.invalid"
        self.hosts[value] = anonymized
        return anonymized

    def avatar_token(self, value: str) -> str:
        """
        Generate a unique, stable replacement for avatar URL tokens.

        The replacement:
        - Has the same length as the original token.
        - Preserves dash positions.
        - Depends only on the input value (not on mapping order or count),
          so the same token always maps to the same anonymized token,
          even when new avatars are added later.
        """
        if value in self.avatars:
            return self.avatars[value]

        # Derive a reproducible hex pattern from the value itself.
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()

        chars = []
        j = 0
        for ch in value:
            if ch == "-":
                chars.append("-")
            else:
                chars.append(digest[j % len(digest)])
                j += 1

        anonymized = "".join(chars)
        self.avatars[value] = anonymized
        return anonymized

    @classmethod
    def from_file(cls, path: Path) -> "MappingStore":
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        kwargs = {field_name: raw.get(field_name, {}) for field_name in _MAPPING_FIELDS}
        store = cls(**kwargs)
        # Legacy: emails stored email -> local part; ensure user_id(email) returns it.
        for e, local in store.emails.items():
            store.users.setdefault(e, local)
        return store

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {field_name: getattr(self, field_name) for field_name in _MAPPING_FIELDS}
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

