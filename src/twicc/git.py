"""Git operations for the API.

Provides git log parsing for the GitLog visualization component.
"""

import subprocess

# Maximum number of entries returned to the frontend.
GIT_LOG_MAX_ENTRIES = 200

# We fetch one extra to detect if there are more commits beyond the limit.
_GIT_LOG_FETCH_LIMIT = GIT_LOG_MAX_ENTRIES + 1

# ASCII Unit Separator — safe delimiter that won't appear in commit messages.
_FIELD_SEP = "\x1f"

# git log --pretty format using unit separator between fields.
# Fields: hash, parents, branch-ref, subject, committer-date, author-date, author-name, author-email
_GIT_LOG_FORMAT = _FIELD_SEP.join(["%h", "%p", "%S", "%s", "%cd", "%ad", "%an", "%ae"])

# Timeout for the git subprocess (seconds).
_GIT_TIMEOUT = 10


def _parse_git_log_line(line: str) -> dict | None:
    """Parse a single line of git log output into a GitLogEntry dict.

    Returns None if the line cannot be parsed (malformed or empty).
    """
    parts = line.split(_FIELD_SEP)
    if len(parts) != 8:
        return None

    hash_, parents_str, branch, message, committer_date, author_date, author_name, author_email = parts

    parents = parents_str.split() if parents_str.strip() else []

    entry = {
        "hash": hash_,
        "branch": branch,
        "parents": parents,
        "message": message,
        "committerDate": committer_date.strip(),
    }

    # Optional author date (may differ from committerDate on rebase/amend).
    if author_date.strip():
        entry["authorDate"] = author_date.strip()

    # Optional author info.
    name = author_name.strip() if author_name else None
    email = author_email.strip() if author_email else None
    if name or email:
        author = {}
        if name:
            author["name"] = name
        if email:
            author["email"] = email
        entry["author"] = author

    return entry


def _get_head_hash(git_directory: str) -> str | None:
    """Return the abbreviated HEAD commit hash, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_index_status(git_directory: str) -> dict | None:
    """Return working-tree file counts {modified, added, deleted}, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    modified = 0
    added = 0
    deleted = 0

    for line in result.stdout.splitlines():
        if len(line) < 2:
            continue
        # git status --porcelain: XY filename
        # X = index status, Y = worktree status
        # We look at both columns to count all changes.
        xy = line[:2]
        if "M" in xy:
            modified += 1
        elif "D" in xy:
            deleted += 1
        elif "?" in xy:
            added += 1
        elif "A" in xy:
            added += 1

    if modified == 0 and added == 0 and deleted == 0:
        return None

    return {"modified": modified, "added": added, "deleted": deleted}


def get_git_log(git_directory: str) -> dict:
    """Run ``git log`` and return parsed entries for the GitLog component.

    Args:
        git_directory: Absolute path to the root of the git repository.

    Returns:
        A dict with keys:
        - ``entries``: list of GitLogEntry dicts (max :data:`GIT_LOG_MAX_ENTRIES`).
        - ``has_more``: True if there are more commits beyond the limit.
        - ``head_commit_hash``: abbreviated hash of HEAD (or None).

    Raises:
        GitError: If the git command fails.
    """
    cmd = [
        "git",
        "-C",
        git_directory,
        "log",
        "--exclude=refs/stash",
        "--exclude=refs/remotes/*/HEAD",
        "--all",
        f"-{_GIT_LOG_FETCH_LIMIT}",
        f"--pretty=format:{_GIT_LOG_FORMAT}",
        "--date=iso",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError("Git log timed out")
    except FileNotFoundError:
        raise GitError("Git is not installed or not in PATH")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(f"Git log failed: {stderr}" if stderr else "Git log failed")

    stdout = result.stdout
    if not stdout.strip():
        return {"entries": [], "has_more": False}

    lines = stdout.strip().split("\n")

    entries = []
    for line in lines:
        entry = _parse_git_log_line(line)
        if entry is not None:
            entries.append(entry)

    has_more = len(entries) > GIT_LOG_MAX_ENTRIES
    if has_more:
        entries = entries[:GIT_LOG_MAX_ENTRIES]

    return {
        "entries": entries,
        "has_more": has_more,
        "head_commit_hash": _get_head_hash(git_directory),
        "index_status": _get_index_status(git_directory),
    }


class GitError(Exception):
    """Raised when a git operation fails."""
