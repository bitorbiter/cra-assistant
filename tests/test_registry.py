"""The committed registry is valid, and invalid registries are rejected loudly."""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from cra_assistant.models import Parser, Source, TrustTier
from cra_assistant.registry import DEFAULT_REGISTRY_PATH, load_registry

VALID_ENTRY = """
    [[sources]]
    id = "cra-eurlex-en"
    citation_prefix = "cra-en"
    short_title = "Regulation (EU) 2024/2847"
    title = "Regulation (EU) 2024/2847"
    url = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R2847"
    lang = "en"
    tier = "trusted"
    licence = "© European Union"
    parser = "eurlex-html"
"""


def write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_committed_registry_validates() -> None:
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assert registry.sources


def test_committed_registry_covers_the_cra_in_both_languages() -> None:
    registry = load_registry()
    regulation = {
        source.lang: source
        for source in registry.sources
        if source.parser is Parser.EURLEX_HTML and source.tier is TrustTier.TRUSTED
    }
    assert regulation.keys() >= {"de", "en"}
    assert all("32024R2847" in str(source.url) for source in regulation.values())


def test_committed_registry_has_both_tiers() -> None:
    tiers = {source.tier for source in load_registry().sources}
    assert tiers == {TrustTier.TRUSTED, TrustTier.UNTRUSTED}


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path, VALID_ENTRY + VALID_ENTRY)

    with pytest.raises(ValidationError, match="duplicate source ids: cra-eurlex-en"):
        load_registry(path)


def test_unknown_tier_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path, VALID_ENTRY.replace('"trusted"', '"semi-trusted"'))

    with pytest.raises(ValidationError, match="tier"):
        load_registry(path)


def test_unknown_parser_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path, VALID_ENTRY.replace('"eurlex-html"', '"pdf"'))

    with pytest.raises(ValidationError, match="parser"):
        load_registry(path)


def test_facts_about_a_fetch_are_rejected(tmp_path: Path) -> None:
    """Checksums belong in the fetch manifest, so a Source must refuse one."""
    path = write_registry(tmp_path, VALID_ENTRY + '    checksum = "sha256:abc"\n')

    with pytest.raises(ValidationError, match="checksum"):
        load_registry(path)


def test_empty_registry_is_rejected(tmp_path: Path) -> None:
    path = write_registry(tmp_path, "sources = []\n")

    with pytest.raises(ValidationError, match="registry contains no sources"):
        load_registry(path)


def test_a_source_is_immutable() -> None:
    source = load_registry().sources[0]

    with pytest.raises(ValidationError):
        source.tier = TrustTier.UNTRUSTED  # type: ignore[misc]


def test_tier_is_a_plain_string_when_serialised() -> None:
    """Tier travels onto documents and segments at ingest, so it must survive
    serialisation as the same literal the registry declares."""
    source = Source.model_validate(
        {
            "id": "example-source",
            "citation_prefix": "example",
            "short_title": "Example",
            "title": "Example",
            "url": "https://example.org/",
            "lang": "en",
            "tier": "untrusted",
            "licence": "UNKNOWN",
            "parser": "generic-html",
        }
    )
    assert source.model_dump(mode="json")["tier"] == "untrusted"
