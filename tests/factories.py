"""Builders for test data, so a new required field is added in one place."""

from datetime import UTC, datetime, timedelta

from cra_assistant.manifest import FetchObservation
from cra_assistant.models import Parser, Source, TrustTier


def make_source(source_id: str = "example-source", **overrides: object) -> Source:
    fields: dict[str, object] = {
        "id": source_id,
        "citation_prefix": source_id.replace("_", "-"),
        "short_title": "Example Work",
        "title": "Example",
        "url": f"https://example.org/{source_id}",
        "lang": "en",
        "tier": TrustTier.UNTRUSTED,
        "licence": "UNKNOWN",
        "parser": Parser.GENERIC_HTML,
    }
    return Source.model_validate(fields | overrides)


def make_observation(
    source_id: str, checksum: str, *, age: timedelta = timedelta()
) -> FetchObservation:
    return FetchObservation(
        source_id=source_id,
        retrieved_at=datetime.now(UTC) - age,
        requested_url=f"https://example.org/{source_id}",
        resolved_url=f"https://example.org/{source_id}",
        http_status=200,
        content_type="text/html",
        byte_count=4,
        checksum=checksum,
        stored_path=f"raw/{source_id}/abc123abc123.html",
    )
