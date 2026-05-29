"""Single source of truth for application version metadata."""

version = "1.0.0"
description = "Initial version registry implementation"


def get_version_metadata() -> tuple[str, str]:
    """Returns normalized current application version metadata."""
    normalized_version = str(version or "").strip()
    normalized_description = str(description or "").strip()
    if not normalized_version:
        normalized_version = "0.0.0"
    return normalized_version, normalized_description
