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
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "users": self.users,
            "emails": self.emails,
            "urls": self.urls,
            "strings": self.strings,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

