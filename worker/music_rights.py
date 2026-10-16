"""Music license interpretation: pure, shared by music_search and the
backend's MCP fast path (list_assets prints it for every music asset)."""

import re

_CC_FAMILY_CODE = r"by-nc-nd|by-nc-sa|by-nc|by-nd|by-sa|by"


def _license_family(license_id):
    """Return a narrow normalized family only for recognized CC/PD terms.

    This is an interpretation used to derive simple capability signals. The
    raw provider value remains separately preserved and is always the value a
    caller should inspect when it needs provenance.
    """
    lid = str(license_id or "").strip().lower().replace("_", "-")
    if not lid:
        return None
    # For URLs, accept only Creative Commons' own host/path vocabulary. A
    # random URL with ``?by=...`` or prose containing the word "by" is not a
    # license and must remain unknown.
    if "://" in lid:
        if not re.match(
                r"^https?://(?:www\.)?creativecommons\.org/", lid):
            return None
        if ("/publicdomain/zero/" in lid or
                "/publicdomain/mark/" in lid):
            return "public-domain"
        match = re.search(
            rf"/licenses/({_CC_FAMILY_CODE})(?:/|$)", lid)
        return match.group(1) if match else None

    compact = re.sub(r"[\s]+", "-", lid).strip("-/")
    if re.fullmatch(r"(?:cc-?)?0(?:[- ]?1(?:\.0)?)?|cc0(?:-1\.0)?|"
                    r"pdm(?:-1\.0)?|public-domain-mark(?:-1\.0)?",
                    compact):
        return "public-domain"
    match = re.fullmatch(
        rf"(?:cc-)?({_CC_FAMILY_CODE})(?:-v?\d+(?:\.\d+)*)?",
        compact)
    return match.group(1) if match else None


def _license_capabilities(license_id):
    """Mechanical rights signals for a recognized provider license value.

    ``None`` means unknown, not false and never a permissive default.
    """
    family = _license_family(license_id)
    if not family:
        return {
            "normalized_license_family": None,
            "commercial_use_allowed": None,
            "attribution_required": None,
            "derivatives_allowed": None,
        }
    public_domain = family == "public-domain"
    return {
        "normalized_license_family": family,
        "commercial_use_allowed": (True if public_domain else
                                   "nc" not in family),
        "attribution_required": False if public_domain else True,
        "derivatives_allowed": (True if public_domain else
                                "nd" not in family),
    }


def provenance(item):
    """Stable, JSON-safe provider provenance with explicit unknowns.

    New catalog hits carry each raw field directly. Compatibility fallbacks
    read older cached hits/assets without manufacturing a label or canonical
    landing page that was never captured.
    """
    item = item or {}
    provider = item.get("provider") or item.get("source") or None
    result_id = item.get("id") or item.get("result_id") or None
    candidate_id = item.get("provider_candidate_id")
    if (candidate_id is None and provider and result_id
            and str(result_id).startswith(f"{provider}:")):
        candidate_id = str(result_id).split(":", 1)[1] or None
    raw_id = item.get("provider_reported_license_id")
    if raw_id is None:
        # ``license`` is the compatibility field historically persisted from
        # the catalog response. It is raw/composed provider text, not our note.
        raw_id = item.get("license") or None
    raw_label = item.get("provider_reported_license_label") or None
    raw_version = item.get("provider_reported_license_version") or None
    raw_url = item.get("provider_reported_license_url") or None
    raw_license_basis = raw_id or raw_label or raw_url
    interpreted = _license_capabilities(raw_license_basis)
    provider_metadata_exposed = bool(
        provider and raw_license_basis and
        interpreted["normalized_license_family"])
    if not provider_metadata_exposed:
        interpreted = {
            "normalized_license_family": None,
            "commercial_use_allowed": None,
            "attribution_required": None,
            "derivatives_allowed": None,
        }
    canonical = (item.get("canonical_source_url") or
                 item.get("page_url") or None)
    return {
        "provider": provider,
        "provider_candidate_id": candidate_id,
        "provider_reported_license_id": raw_id,
        "provider_reported_license_label": raw_label,
        "provider_reported_license_version": raw_version,
        "provider_reported_license_url": raw_url,
        "license_verification_status": (
            "provider_metadata_exposed"
            if provider_metadata_exposed else "unknown"),
        **interpreted,
        "canonical_source_url": canonical,
        # A legacy source URL may be a landing page or a media endpoint. Keep
        # it visible, but do not relabel it canonical without direct evidence.
        "source_url": item.get("source_url") or canonical,
        "creator": (item.get("creator") or item.get("artist") or
                    item.get("author") or None),
        "provider_retrieved_at": item.get("provider_retrieved_at") or None,
        "downloaded_sha256": item.get("downloaded_sha256") or None,
    }


def _license_note(license_id, author):
    family = _license_family(license_id)
    if family == "public-domain":
        return "public domain — no obligations"
    if not family:
        return ("license UNKNOWN — no use permission established; do not use "
                "until the rights are verified")
    who = author or "the artist"
    if "nd" in family:
        return (f"CC {family.upper()} — NO DERIVATIVES: not usable for "
                "syncing to video")
    if "nc" in family:
        # Checked before by-sa: "by-nc-sa" is NC first — the commercial
        # restriction is the fact that changes what the user may do.
        return (f"CC {family.upper()} — "
                "NON-COMMERCIAL USE ONLY: fine for a personal video, NOT "
                f"for monetized/business content; credit {who} in the "
                "caption/description")
    if family == "by-sa":
        return (f"CC BY-SA — free for commercial use; credit {who} in the "
                "caption/description")
    return (f"CC BY — free for commercial use; credit {who} in the "
            "caption/description")


def license_note(item):
    rights = provenance(item)
    return _license_note(rights["provider_reported_license_id"] or
                         rights["provider_reported_license_label"] or
                         rights["provider_reported_license_url"],
                         rights["creator"])
