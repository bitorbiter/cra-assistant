"""Loading and validating the committed source registry.

The registry is a TOML data file, not Python. A reviewer must be able to read
"source added, marked trusted" off a pull request diff without following any
code. This module is the only thing that turns that file into objects, and it
refuses anything it cannot fully validate.
"""

import tomllib
from collections import Counter
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from cra_assistant.models import Source
from cra_assistant.paths import DEFAULT_REGISTRY_PATH

__all__ = ["DEFAULT_REGISTRY_PATH", "SourceRegistry", "load_registry"]


class SourceRegistry(BaseModel):
    """Every source the corpus may be built from.

    Holding the collection in a model rather than a bare list gives the
    uniqueness invariant somewhere to live.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[Source, ...]

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        # Emptiness is checked here rather than as a field constraint: a
        # min_length on the field also fires when a single entry fails to
        # validate, reporting an empty registry when the real fault is one
        # bad source. An after-validator only runs once the entries parse.
        if not self.sources:
            raise ValueError("registry contains no sources")

        counts = Counter(source.id for source in self.sources)
        duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate source ids: {', '.join(duplicates)}")

        # Segment ids begin with the citation prefix, so a shared prefix would
        # let two sources mint the same segment id.
        prefix_counts = Counter(source.citation_prefix for source in self.sources)
        shared = sorted(prefix for prefix, count in prefix_counts.items() if count > 1)
        if shared:
            raise ValueError(f"duplicate citation prefixes: {', '.join(shared)}")
        return self


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> SourceRegistry:
    """Read and validate the registry at ``path``.

    Raises ``tomllib.TOMLDecodeError`` if the file is not TOML and pydantic's
    ``ValidationError`` if any entry is malformed — an unknown tier, a duplicate
    id, a missing field, or a key that does not belong on a source. Both are
    left to propagate: they name the offending entry and field precisely, and
    wrapping them in a project-specific exception would only hide that.
    """
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return SourceRegistry.model_validate(raw)
