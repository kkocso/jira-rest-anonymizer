"""
JIRA REST API response anonymizer.

This package provides:
- A CLI entry point (see `jira_anonymizer.cli`)
- An `Anonymizer` class for programmatic use
"""

from .anonymizer import Anonymizer  # noqa: F401
from .config import AnonymizerConfig  # noqa: F401
from .mapping_store import MappingStore  # noqa: F401

