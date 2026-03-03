from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping

from .config import AnonymizerConfig
from .mapping_store import MappingStore


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

    def anonymize(self, data: Any) -> Any:
        """
        Return an anonymized deep copy of `data`.
        """
        return self._walk(deepcopy(data))

    # Internal helpers -----------------------------------------------------

    def _walk(self, node: Any, parent_key: str | None = None) -> Any:
        if isinstance(node, dict):
            return self._walk_dict(node)
        if isinstance(node, list):
            return [self._walk(item, parent_key=parent_key) for item in node]
        return node

    def _walk_dict(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        # Special handling for JIRA user objects.
        if self._config.anonymize_users and self._looks_like_user(obj):
            return self._anonymize_user_object(obj)

        new_obj: Dict[str, Any] = {}

        for key, value in obj.items():
            # Handle customfield renames at key level.
            new_key = self._rename_customfield_key(key)

            # Primitive field replacements based on key.
            if isinstance(value, str):
                new_obj[new_key] = self._anonymize_primitive_field(new_key, value)
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
        if isinstance(account_id, str):
            user["accountId"] = self._mappings.user_id(account_id)

        if self._config.anonymize_emails:
            email = user.get("emailAddress")
            if isinstance(email, str):
                user["emailAddress"] = self._mappings.email(email)

        display_name = user.get("displayName")
        if isinstance(display_name, str):
            # Display name gets a stable generic label from the user mapping.
            anon_id = self._mappings.user_id(display_name)
            user["displayName"] = f"User {anon_id.split('_')[-1]}"

        name = user.get("name")
        if isinstance(name, str):
            anon_id = self._mappings.user_id(name)
            user["name"] = anon_id

        return {k: self._walk(v, parent_key=k) for k, v in user.items()}

    # Primitive fields -----------------------------------------------------

    def _anonymize_primitive_field(self, key: str, value: str) -> str:
        # Emails
        if self._config.anonymize_emails and key == "emailAddress":
            return self._mappings.email(value)

        # User identifiers
        if key in {"accountId", "name"}:
            return self._mappings.user_id(value)

        if key == "displayName":
            anon_id = self._mappings.user_id(value)
            return f"User {anon_id.split('_')[-1]}"

        # URLs
        if self._config.anonymize_urls and key == "self":
            return self._mappings.url(value)

        # Customfield values (if configured)
        if self._config.anonymize_customfield_values and key.startswith("customfield_"):
            return self._mappings.string(value)

        return value

    # Customfield key renaming ---------------------------------------------

    def _rename_customfield_key(self, key: str) -> str:
        if not key.startswith("customfield_"):
            return key

        # First allow explicit mappings.
        if key in self._customfield_map:
            return self._customfield_map[key]

        # Otherwise leave as-is. If you later want automatic renaming,
        # you could generate stable names via MappingStore here.
        return key

