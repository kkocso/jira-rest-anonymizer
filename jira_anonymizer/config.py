from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass
class AnonymizerConfig:
    anonymize_users: bool = True
    anonymize_emails: bool = True
    anonymize_urls: bool = True
    anonymize_customfield_values: bool = True
    customfield_map_path: str | None = None


def load_config(path: Path) -> AnonymizerConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return AnonymizerConfig(
        anonymize_users=bool(raw.get("anonymize_users", True)),
        anonymize_emails=bool(raw.get("anonymize_emails", True)),
        anonymize_urls=bool(raw.get("anonymize_urls", True)),
        anonymize_customfield_values=bool(raw.get("anonymize_customfield_values", True)),
        customfield_map_path=raw.get("customfield_map_path"),
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

