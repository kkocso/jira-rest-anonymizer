from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


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

    def email(self, value: str) -> str:
        # Use a deterministic fake domain to avoid accidentally looking real.
        local = self._get_or_create(value, self.emails, "user")
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

    def avatar_token(self, value: str) -> str:
        """
        Generate a unique, stable replacement for avatar URL tokens.

        The replacement:
        - Has the same length as the original token.
        - Preserves dash positions.
        """
        if value in self.avatars:
            return self.avatars[value]

        # Use the current avatar count as a simple, stable index to derive
        # a hex-only pattern source. This avoids relying on _next_id, which
        # expects values in the target store to be prefixed with "avatar_".
        idx = len(self.avatars) + 1
        pattern_source = f"{idx:x}" + "0123456789abcdef"

        chars = []
        j = 0
        for ch in value:
            if ch == "-":
                chars.append("-")
            else:
                chars.append(pattern_source[j % len(pattern_source)])
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
        return cls(
            users=raw.get("users", {}),
            emails=raw.get("emails", {}),
            urls=raw.get("urls", {}),
            strings=raw.get("strings", {}),
            customfields=raw.get("customfields", {}),
            numbers=raw.get("numbers", {}),
            project_keys=raw.get("project_keys", {}),
            avatars=raw.get("avatars", {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "users": self.users,
            "emails": self.emails,
            "urls": self.urls,
            "strings": self.strings,
            "customfields": self.customfields,
            "numbers": self.numbers,
            "project_keys": self.project_keys,
            "avatars": self.avatars,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

