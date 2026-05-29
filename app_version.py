"""Single source of truth for application version metadata."""

import subprocess
import os

version = "1.0.2"
description = "Improved selectable version history admin UI"


def get_version_metadata() -> tuple[str, str]:
    """Returns normalized current application version metadata."""
    normalized_version = str(version or "").strip()
    normalized_description = str(description or "").strip()
    if not normalized_version:
        normalized_version = "0.0.0"
    return normalized_version, normalized_description


def get_git_commit() -> str | None:
    """Returns the current Git commit hash (8-char short form), or None.

    Resolution order:
    1. Live ``git rev-parse HEAD`` subprocess (works on dev host / CI).
    2. /app/.git_commit file baked into the Docker image at build time.

    Returns None when neither source is available (e.g. bare checkout
    without git installed and no baked file).
    """
    # Strategy 1: live git
    try:
        repo_root = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()[:8]
            if commit:
                return commit
    except Exception:
        pass

    # Strategy 2: baked file written by Dockerfile ARG GIT_COMMIT
    try:
        baked_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git_commit")
        with open(baked_file, "r") as fh:
            commit = fh.read().strip()[:8]
            if commit and commit != "unknown":
                return commit
    except Exception:
        pass

    return None
