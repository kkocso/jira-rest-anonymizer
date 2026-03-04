from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass
class AnonymizerConfig:
    anonymize_users: bool = True
    anonymize_account_ids: bool = True
    anonymize_emails: bool = True
    anonymize_display_names: bool = True
    anonymize_urls: bool = True
    anonymize_customfield_values: bool = True
    customfield_map_path: str | None = None
    activity_start_timestamp: str | None = None
    activity_end_timestamp: str | None = None


def _get_bool(raw: Dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    return bool(value)


def load_config(path: Path) -> AnonymizerConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return AnonymizerConfig(
        anonymize_users=_get_bool(raw, "anonymize_users", True),
        anonymize_account_ids=_get_bool(raw, "anonymize_account_ids", True),
        anonymize_emails=_get_bool(raw, "anonymize_emails", True),
        anonymize_display_names=_get_bool(raw, "anonymize_display_names", True),
        anonymize_urls=_get_bool(raw, "anonymize_urls", True),
        anonymize_customfield_values=_get_bool(raw, "anonymize_customfield_values", True),
        customfield_map_path=raw.get("customfield_map_path"),
        activity_start_timestamp=raw.get("activity_start_timestamp"),
        activity_end_timestamp=raw.get("activity_end_timestamp"),
    )


def load_customfield_map(path: Path | None) -> Dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Customfield map file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    # Expect a simple mapping of string->string
    return {str(k): str(v) for k, v in data.items()}

