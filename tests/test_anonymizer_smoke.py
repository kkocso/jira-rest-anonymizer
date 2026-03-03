from pathlib import Path
import json

from jira_anonymizer.anonymizer import Anonymizer
from jira_anonymizer.config import load_config, load_customfield_map
from jira_anonymizer.mapping_store import MappingStore


def test_deterministic_anonymization(tmp_path: Path) -> None:
    base = Path(__file__).parents[1]
    raw_path = base / "examples" / "raw-issues.json"
    config_path = base / "config" / "default-config.yml"
    customfield_map_path = base / "config" / "customfield-map.example.yml"
    mapping_store_path = tmp_path / "mapping.json"

    config = load_config(config_path)
    customfield_map = load_customfield_map(customfield_map_path)
    mapping_store = MappingStore.from_file(mapping_store_path)

    with raw_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    anonymizer = Anonymizer(
        config=config,
        customfield_map=customfield_map,
        mapping_store=mapping_store,
    )
    result1 = anonymizer.anonymize(data)
    result2 = anonymizer.anonymize(data)

    # Deterministic: two runs over the same data should produce equal results.
    assert result1 == result2

    # Sensitive fields should be changed.
    reporter = result1["issues"][0]["fields"]["reporter"]
    assert reporter["accountId"].startswith("user_")
    assert reporter["emailAddress"].endswith("@example.invalid")

