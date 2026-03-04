from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .anonymizer import Anonymizer
from .config import AnonymizerConfig, load_config, load_customfield_map
from .mapping_store import MappingStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Anonymize JIRA REST API JSON responses.",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        required=True,
        help="Path to input JSON file.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Path to write anonymized JSON.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config/default-config.yml",
        help="Path to YAML config file (default: config/default-config.yml).",
    )
    parser.add_argument(
        "--customfield-map",
        type=str,
        default=None,
        help="Optional path to a customfield mapping YAML file.",
    )
    parser.add_argument(
        "--mapping-store",
        type=str,
        default=".anonymizer-mapping.json",
        help="Path to JSON file used to persist deterministic mappings.",
    )
    return parser.parse_args(argv)


def run_anonymization(
    input_path: Path,
    output_path: Path,
    config_path: Path,
    customfield_map_path: Path | None,
    mapping_path: Path,
) -> None:
    """
    Core orchestration for running the anonymizer once.

    This is separated from `main` so it can be called directly from
    tests or other code without going through argparse / subprocess.
    """

    config: AnonymizerConfig = load_config(config_path)

    effective_customfield_map_path = customfield_map_path
    if not effective_customfield_map_path and config.customfield_map_path:
        effective_customfield_map_path = Path(config.customfield_map_path)

    customfield_map = load_customfield_map(effective_customfield_map_path)
    mapping_store = MappingStore.from_file(mapping_path)

    with input_path.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)

    anonymizer = Anonymizer(
        config=config,
        customfield_map=customfield_map,
        mapping_store=mapping_store,
    )
    anonymized = anonymizer.anonymize(data)

    mapping_store.save(mapping_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(anonymized, f, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    config_path = Path(args.config)
    mapping_path = Path(args.mapping_store)

    customfield_map_path = Path(args.customfield_map) if args.customfield_map else None

    run_anonymization(
        input_path=input_path,
        output_path=output_path,
        config_path=config_path,
        customfield_map_path=customfield_map_path,
        mapping_path=mapping_path,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

