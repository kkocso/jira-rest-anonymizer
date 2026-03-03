# jira-rest-anonymizer

Command line tool and Python library to **anonymize JIRA REST API JSON responses** so that they can be safely shared or used as mock data without leaking sensitive information like user identifiers, email addresses, or internal URLs.

## Features

- **Deterministic pseudonymization** of:
  - JIRA user identifiers (e.g. `accountId`, `name`, `displayName`)
  - Email addresses
  - `self` links and other URLs found in JIRA responses
- **Custom field support**:
  - Rename `customfield_12345` → `customfield_story_points` (or any friendly name)
  - Optionally anonymize custom field values
- **Config driven**:
  - YAML configuration to fine tune what gets anonymized
  - Optional persistent mapping store so the same real value always maps to the same fake value across runs
- **Safe by default**:
  - Outputs JSON with the same shape as the input, ready to be used as fixtures or shared samples.

## Installation

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Quick start

1. Save a JIRA REST API JSON response (for example the result of `/rest/api/3/search`) into a file:

```bash
curl -u "$JIRA_USER:$JIRA_TOKEN" \
  -H "Accept: application/json" \
  "https://your-domain.atlassian.net/rest/api/3/search?jql=project=DEMO" \
  > examples/raw-issues.json
```

2. Run the anonymizer:

```bash
python -m jira_anonymizer.cli \
  --input examples/raw-issues.json \
  --output examples/anonymized-issues.json \
  --config config/default-config.yml \
  --customfield-map config/customfield-map.example.yml \
  --mapping-store .anonymizer-mapping.json
```

3. Inspect `examples/anonymized-issues.json`. It will have the same structure as the original response, but sensitive values will be replaced with deterministic pseudonyms.

## Configuration

The main configuration lives in `config/default-config.yml`:

- **`anonymize_users`**: anonymize JIRA user objects (`reporter`, `assignee`, etc.).
- **`anonymize_emails`**: anonymize any `emailAddress` fields.
- **`anonymize_urls`**: anonymize URLs such as `self` links.
- **`anonymize_customfield_values`**: anonymize values inside custom fields.
- **`customfield_map_path`**: optional path to a YAML file describing how to rename custom field IDs.

Example `customfield-map`:

```yaml
customfield_10000: customfield_story_points
customfield_10001: customfield_epic_link
customfield_10002: customfield_team
```

If the anonymizer encounters a key named `customfield_10026`, it will be renamed to `customfield_story_points` everywhere in the JSON (e.g. in `fields` and metadata objects) while keeping the field value (optionally anonymized).

## Programmatic usage

You can also use the anonymizer as a library:

```python
from pathlib import Path
import json

from jira_anonymizer.config import load_config, load_customfield_map
from jira_anonymizer.mapping_store import MappingStore
from jira_anonymizer.anonymizer import Anonymizer

config = load_config(Path("config/default-config.yml"))
customfield_map = load_customfield_map(Path("config/customfield-map.example.yml"))
mapping_store = MappingStore.from_file(Path(".anonymizer-mapping.json"))

with open("examples/raw-issues.json", "r", encoding="utf-8") as f:
    data = json.load(f)

anonymizer = Anonymizer(config=config, customfield_map=customfield_map, mapping_store=mapping_store)
anonymized = anonymizer.anonymize(data)

mapping_store.save(Path(".anonymizer-mapping.json"))

with open("examples/anonymized-issues.json", "w", encoding="utf-8") as f:
    json.dump(anonymized, f, indent=2, ensure_ascii=False)
```

