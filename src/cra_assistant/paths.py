"""Default locations, resolved relative to the source tree.

These work in a checkout, which is where the registry is edited and the corpus
is built. They do not survive installation as a wheel, because the registry and
the data directory live beside the package rather than inside it. Every entry
point takes an explicit override, so making these configuration is deferred
until something actually needs it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_REGISTRY_PATH = REPO_ROOT / "registry" / "sources.toml"
"""Committed: the sources and their trust tiers."""

DEFAULT_PINS_PATH = REPO_ROOT / "registry" / "pins.toml"
"""Committed: the checksums a human has approved."""

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
"""Not committed: fetched bytes and the observation manifest."""
