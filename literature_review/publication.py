"""Normalize publication-source metadata without inferring venue from PDF URLs."""

import re

from literature_review.models import PublicationVenue


def publication_source(
    name: str | None,
    *,
    provider: str,
    metadata_path: str,
    source_id: str | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    version: str | None = None,
) -> PublicationVenue | None:
    """Reject repository/preprint metadata as proof of a publication venue.

    A missing source type is retained for compatibility with older provider
    records, but always labeled provider_reported rather than verified.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    if (source_type or "").lower() in {"repository", "preprint"}:
        return None
    if version == "submittedVersion":
        return None
    if re.search(r"\b(?:arxiv|biorxiv|medrxiv)\b|\bpreprints?\b|\brepository\b", name, re.IGNORECASE):
        return None
    return PublicationVenue(
        name=name.strip(), provider=provider, metadata_path=metadata_path,
        source_id=source_id, source_type=source_type, source_url=source_url,
    )


def openalex_publication_venues(record: dict) -> list[PublicationVenue]:
    """Check all locations, preferring primary publication metadata."""
    locations = [("primary_location", record.get("primary_location") or {})]
    locations.extend((f"locations[{index}]", location or {}) for index, location in enumerate(record.get("locations") or []))
    locations.append(("best_oa_location", record.get("best_oa_location") or {}))
    venues = []
    seen = set()
    for path, location in locations:
        source = location.get("source") or {}
        venue = publication_source(
            source.get("display_name"), provider="openalex", metadata_path=f"{path}.source",
            source_id=source.get("id"), source_type=source.get("type"),
            source_url=location.get("landing_page_url"), version=location.get("version"),
        )
        if venue is None:
            continue
        key = (venue.source_id, venue.name.casefold())
        if key not in seen:
            venues.append(venue)
            seen.add(key)
    return venues


def ss_publication_venues(record: dict) -> list[PublicationVenue]:
    """Prefer structured publicationVenue; use legacy venue only if absent."""
    structured = record.get("publicationVenue") or {}
    if structured.get("name"):
        venue = publication_source(
            structured["name"], provider="semantic_scholar", metadata_path="publicationVenue",
            source_id=structured.get("id"), source_type=structured.get("type"),
            source_url=structured.get("url"),
        )
    else:
        venue = publication_source(
            record.get("venue"), provider="semantic_scholar", metadata_path="venue",
        )
    return [venue] if venue else []
