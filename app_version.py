"""Single source of truth for application version metadata."""

import subprocess
import os

version = '1.0.214'
description = 'Удаление ещё больше романтики'


def get_version_metadata() -> tuple[str, str]:
    """Returns normalized current application version metadata."""
    normalized_version = str(version or "").strip()
    normalized_description = str(description or "").strip()
    if not normalized_version:
        normalized_version = "0.0.0"
    return normalized_version, normalized_description


def _get_repo_root() -> str:
    """Returns the git repository root, checking /workspace and file directory."""
    file_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir("/workspace/.git"):
        return "/workspace"
    return file_dir


def get_git_commit() -> str | None:
    """Returns the current Git commit hash (8-char short form), or None.

    Resolution order:
    1. /app/.git_commit file baked into the Docker image at build time (True running commit).
    2. Live ``git rev-parse HEAD`` subprocess (works on dev host / CI / fallback).
    """
    # Strategy 1: baked file written by Dockerfile ARG GIT_COMMIT
    try:
        baked_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git_commit")
        if os.path.exists(baked_file):
            with open(baked_file, "r") as fh:
                commit = fh.read().strip()[:8]
                if commit and commit != "unknown":
                    return commit
    except Exception:
        pass

    # Strategy 2: live git
    try:
        repo_root = _get_repo_root()
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

    return None


def get_git_status_info() -> dict:
    """Returns a dictionary with live Git status information.

    Keys:
    - branch: str or None
    - commit: str or None
    - total_commits: int or None
    - is_clean: bool
    - modified_files: list of str
    """
    repo_root = _get_repo_root()
    info = {
        "branch": None,
        "commit": None,
        "total_commits": 0,
        "is_clean": True,
        "modified_files": []
    }

    # 1. Get branch
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            info["branch"] = res.stdout.strip()
    except Exception:
        pass

    # 2. Get commit (short hash, e.g. 7 chars)
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            info["commit"] = res.stdout.strip()
    except Exception:
        pass

    if not info["commit"]:
        # Fall back to get_git_commit()
        info["commit"] = get_git_commit()

    # 3. Get total commits
    try:
        res = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            info["total_commits"] = int(res.stdout.strip())
    except Exception:
        pass

    # 4. Get modified files using git status --porcelain
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            modified = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Format XY PATH
                parts = line.split(None, 1)
                if len(parts) > 1:
                    path = parts[1].strip('"')
                    modified.append(path)
                else:
                    modified.append(line)
            info["modified_files"] = modified
            info["is_clean"] = len(modified) == 0
    except Exception:
        pass

    return info

