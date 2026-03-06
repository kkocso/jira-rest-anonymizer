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

2. (Optional) Create your own customfield map by copying the example:

```bash
cp config/customfield-map.example.yml config/customfield-map.yml
```

Edit `config/customfield-map.yml` to match your own JIRA custom field IDs.

3. Run the anonymizer:

```bash
python -m jira_anonymizer.cli \
  --input examples/raw-issues.json \
  --output examples/anonymized-issues.json \
  --config config/default-config.yml \
  --customfield-map config/customfield-map.yml \
  --mapping-store .anonymizer-mapping.json
```

3. Inspect `examples/anonymized-issues.json`. It will have the same structure as the original response, but sensitive values will be replaced with deterministic pseudonyms.

## Configuration

The main configuration lives in `config/default-config.yml`:

- **`anonymize_users`**: anonymize JIRA user objects (`reporter`, `assignee`, etc.).
- **`anonymize_account_ids`**: anonymize `accountId` (and `name`) user identifiers.
- **`anonymize_emails`**: anonymize any `emailAddress` fields.
- **`anonymize_display_names`**: anonymize `displayName` fields. When an email address is present, the display name is derived from the same underlying anonymized identifier so you can immediately see which display name belongs to which email.
- **`anonymize_urls`**: anonymize URLs such as `self` links by randomizing numeric identifiers inside them (e.g. issue IDs) while keeping the overall URL shape.
- **`anonymize_customfield_values`**: anonymize values inside custom fields.
- **`customfield_map_path`**: optional path to a YAML file describing how to rename custom field IDs.
 - **`activity_start_timestamp` / `activity_end_timestamp`**: optional activity window used to filter time-based entries such as worklogs and changelog histories. Timestamps must be in the form `YYYY-MM-DDTHH:MM:SS.sss+ZZZZ` (for example `2026-02-24T12:29:54.873+0100`). Only entries with a `created` (or `started`) timestamp within this inclusive range are kept.

Long text fields such as `description`, `comment`, or `body` are additionally scrubbed to remove embedded sensitive information:

- Email addresses inside the text are anonymized.
- Most standalone numbers are anonymized (with a few exceptions for formatting markers like `h2`).
- Sequences of capitalized words that look like company names are replaced with deterministic pseudonyms.
- Inline JIRA accountId references like `[~accountid:5b1b2ae23e12f12be]` are also anonymized, with the accountId part replaced by a deterministic user identifier.

Example `customfield-map.yml`:

```yaml
customfield_10000: customfield_story_points
customfield_10001: customfield_epic_link
customfield_10002: customfield_team
```

If the anonymizer encounters a key named `customfield_10026`, it will be renamed to `customfield_story_points` everywhere in the JSON (e.g. in `fields` and metadata objects) while keeping the field value (optionally anonymized).

You should treat `config/customfield-map.yml` as environment-specific configuration and **not** commit it to version control. This repository's `.gitignore` already excludes it so each team can maintain their own mapping locally.

If a `customfield_*` key is **not** present in the customfield map, it will be renamed to a unique, deterministic pseudonym (for example `customfield_001`, `customfield_002`, …) so that:

- Each original custom field ID gets its own unique replacement.
- The same original ID always maps to the same replacement across runs (as long as the same mapping store is used).

Issue keys in the `key` field (for example `ANDROID-179`) and their numeric `id` fields are also anonymized:

- The project prefix (`ANDROID`) is replaced with a stable fake project code (`JIRAPROJ_001`, `JIRAPROJ_002`, …).
- The numeric part (`179`) is replaced with a stable fake number of the same length.
- The issue `id` (e.g. `10001`) is replaced with a stable fake number of the same length, and the same original `id` always maps to the same anonymized value.

Changelog entries (for example under `changelog.histories`) have their detailed values anonymized:

- `fieldId` values like `customfield_10001` are renamed using the same customfield-mapping logic as other keys.
- Numeric `from` / `to` values (e.g. sprint IDs) are anonymized as deterministic numbers.
- `fromString` / `toString` are treated as rich text, so embedded issue keys (e.g. `ANDROID-123`), email addresses, numbers, and company-like names are all anonymized.

Avatar URLs (for example values under `avatarUrls` like
`"https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/5b1b3aa-c123a12345c5/48"`)
are also anonymized:

- The long avatar token segment (`5b9b6a12e7-8bbc-c464f12345c5`) is replaced with a deterministic pseudonym of the **same length**, preserving dash positions.
- The same original avatar token always maps to the same anonymized token across runs (with the same mapping store).

All attachment data is removed from the output entirely: any `attachment` or `attachments` fields (and their contents) are dropped from the anonymized JSON.

## How it works internally

At a high level the anonymization pipeline works like this:

- **CLI orchestration**: `python -m jira_anonymizer.cli` parses options (`--input`, `--output`, `--config`, `--customfield-map`, `--mapping-store`), then loads config, customfield map, and the persistent mapping store.
- **Prepass for user identities**: before rewriting anything, the anonymizer scans the JSON to find all JIRA user objects and registers their `accountId`, `emailAddress`, `displayName`, and `name` as aliases for a single internal user id.
- **Recursive traversal**: the anonymizer deep-copies the JSON and walks it recursively, dropping attachments, renaming `customfield_*` keys, and routing scalar values through specialized anonymizers.
- **Field-level anonymization**: user dictionaries, rich-text fields, changelog/worklog entries, numeric IDs, URLs (including avatar URLs), issue keys, and customfield values are rewritten according to the configuration and mapping store.
- **Output + persistence**: the anonymized JSON is written to the requested output path and the mapping store is saved so subsequent runs preserve all previously assigned pseudonyms.

## Mapping store (`.anonymizer-mapping.json`)

The anonymizer keeps a **deterministic mapping** from real values to anonymized ones in a JSON file (by default `.anonymizer-mapping.json`):

- It stores mappings for users, emails, URLs, generic strings, customfield keys, numeric IDs, project keys, avatar tokens, etc.
- This ensures that the **same real value** is always replaced by the **same fake value** across runs.
- When a JIRA user object contains multiple identifiers (for example `accountId`, `emailAddress`, `displayName`, and `name`), those identifiers are merged so they all resolve to a single internal `user_N` id, ensuring the same person has consistent anonymized accountId, email, and displayName across files and runs.

You normally do not need to create this file manually:

- If the file does not exist, it will be created automatically on the first run (using the path you pass via `--mapping-store`).
- The repository's `.gitignore` excludes `.anonymizer-mapping.json`, so each environment maintains its own local mapping without committing it to version control.

If you want to start from a clean slate (for example to regenerate all pseudonyms), simply delete `.anonymizer-mapping.json` before running the anonymizer again.

## Programmatic usage

You can also use the anonymizer as a library:

```python
from pathlib import Path
import json

from jira_anonymizer.config import load_config, load_customfield_map
from jira_anonymizer.mapping_store import MappingStore
from jira_anonymizer.anonymizer import Anonymizer

config = load_config(Path("config/default-config.yml"))
customfield_map = load_customfield_map(Path("config/customfield-map.yml"))
mapping_store = MappingStore.from_file(Path(".anonymizer-mapping.json"))

with open("examples/raw-issues.json", "r", encoding="utf-8") as f:
    data = json.load(f)

anonymizer = Anonymizer(config=config, customfield_map=customfield_map, mapping_store=mapping_store)
anonymized = anonymizer.anonymize(data)

mapping_store.save(Path(".anonymizer-mapping.json"))

with open("examples/anonymized-issues.json", "w", encoding="utf-8") as f:
    json.dump(anonymized, f, indent=2, ensure_ascii=False)
```

