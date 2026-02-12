"""Git operations for the API.

Provides git log parsing for the GitLog visualization component,
and file diff retrieval for the Monaco diff editor.
"""

import os
import subprocess

from twicc.file_content import MAX_FILE_SIZE

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


# ---------------------------------------------------------------------------
# File list → tree builder
# ---------------------------------------------------------------------------


def _build_file_tree(files: list[dict], root_name: str = "") -> dict:
    """Build a file-tree structure from a flat list of file entries.

    Each entry in *files* must have at minimum ``{"path": "a/b/c.py"}``.

    **Commit files** carry a ``"status"`` key (a single status string).
    **Index files** carry ``"staged_status"`` and ``"unstaged_status"`` keys
    (each is a status string or None).

    All status-related keys present on the entry are preserved on the tree
    file nodes.

    Returns a tree in the same format as the directory-tree API::

        {
            "name": root_name,
            "type": "directory",
            "loaded": True,
            "children": [
                {
                    "name": "a",
                    "type": "directory",
                    "loaded": True,
                    "children": [
                        {"name": "c.py", "type": "file", "status": "modified"}
                    ]
                }
            ]
        }

    Directories are sorted before files, both alphabetically (case-insensitive).
    """
    _STATUS_KEYS = ("status", "staged_status", "unstaged_status")

    # Build a nested dict first, then convert to the tree format.
    tree: dict = {}

    for entry in files:
        parts = entry["path"].split("/")
        # Collect whichever status keys are present
        statuses = {k: entry[k] for k in _STATUS_KEYS if k in entry}

        current = tree
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if is_last:
                # Leaf file node
                current.setdefault(part, {"__file__": True, **statuses})
            else:
                # Intermediate directory node
                current.setdefault(part, {})
                current = current[part]

    def _convert(name: str, node: dict) -> dict:
        """Recursively convert the nested dict into the tree format."""
        if "__file__" in node:
            result = {"name": name, "type": "file"}
            for k in _STATUS_KEYS:
                if k in node:
                    result[k] = node[k]
            return result

        children = []
        for child_name, child_node in node.items():
            children.append(_convert(child_name, child_node))

        # Sort: directories first, then files, both case-insensitive alphabetical
        children.sort(
            key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower())
        )

        return {
            "name": name,
            "type": "directory",
            "loaded": True,
            "children": children,
        }

    return _convert(root_name, tree)


def _compute_stats(files: list[dict]) -> dict:
    """Compute {modified, added, deleted} counts from a flat file list.

    Works with both commit files (``"status"`` key) and index files
    (``"staged_status"`` / ``"unstaged_status"`` keys).  For index files the
    stat reflects the *most significant* status of the file (staged wins over
    unstaged if both are present; among statuses: deleted > modified > added).
    """
    modified = 0
    added = 0
    deleted = 0

    for f in files:
        # Commit files have "status", index files have "staged_status"/"unstaged_status".
        status = f.get("status") or f.get("staged_status") or f.get("unstaged_status")
        if status in ("modified", "renamed"):
            modified += 1
        elif status in ("added", "untracked"):
            added += 1
        elif status == "deleted":
            deleted += 1

    return {"modified": modified, "added": added, "deleted": deleted}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_current_branch(git_directory: str) -> str | None:
    """Return the current branch name or abbreviated HEAD hash.

    Uses ``git rev-parse --abbrev-ref HEAD`` which handles all cases:
    regular repos, worktrees, and detached HEAD (returns ``HEAD`` in that case,
    so we fall back to the abbreviated commit hash).
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
            # Detached HEAD — return abbreviated hash
            return _get_head_hash(git_directory)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Index (working-tree) changed files
# ---------------------------------------------------------------------------


def _status_letter_to_status(letter: str) -> str | None:
    """Map a single git status letter to a normalized status string."""
    return {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "added",
        "T": "modified",
    }.get(letter)


def _parse_index_files(git_directory: str) -> list[dict] | None:
    """Parse ``git status --porcelain`` into a flat list of file entries.

    Each entry has::

        {
            "path": "...",
            "staged_status": "modified"|"added"|"deleted"|"renamed"|None,
            "unstaged_status": "modified"|"added"|"deleted"|None,
        }

    The ``git status --porcelain`` format is ``XY path`` where:
    - X = index (staged) status
    - Y = worktree (unstaged) status

    A file can be both staged and unstaged (e.g. partially staged changes).
    Untracked files (``??``) are reported as unstaged added.

    Returns None if no changes or on failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    files = []

    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        # git status --porcelain: XY filename
        x = line[0]  # index (staged) status
        y = line[1]  # worktree (unstaged) status
        path = line[3:]

        # Handle renames: "R  old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1]

        # Untracked files: both X and Y are '?'
        if x == "?" and y == "?":
            files.append({
                "path": path,
                "staged_status": None,
                "unstaged_status": "untracked",
            })
            continue

        staged = _status_letter_to_status(x)
        unstaged = _status_letter_to_status(y)

        if staged or unstaged:
            files.append({
                "path": path,
                "staged_status": staged,
                "unstaged_status": unstaged,
            })

    return files if files else None


def get_index_files(git_directory: str) -> dict | None:
    """Return working-tree changed files as stats + file tree.

    Returns a dict with:
    - ``stats``: ``{modified, added, deleted}`` counts
    - ``tree``: file tree in the same format as the directory-tree API

    Returns None if there are no changes.
    """
    files = _parse_index_files(git_directory)
    if not files:
        return None

    return {
        "stats": _compute_stats(files),
        "tree": _build_file_tree(files, root_name="Uncommitted files"),
    }


# ---------------------------------------------------------------------------
# Commit changed files
# ---------------------------------------------------------------------------


def _parse_commit_files(git_directory: str, commit_hash: str) -> list[dict]:
    """Parse ``git diff-tree --name-status`` into a flat list of file entries.

    Returns a list of ``{"path": "...", "status": "modified"|"added"|"deleted"}``.

    Raises GitError on failure.
    """
    cmd = [
        "git",
        "-C",
        git_directory,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--root",       # handles root commits (no parent)
        "-r",           # recurse into sub-trees
        commit_hash,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError("Git diff-tree timed out")
    except FileNotFoundError:
        raise GitError("Git is not installed or not in PATH")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(f"Git diff-tree failed: {stderr}" if stderr else "Git diff-tree failed")

    files = []

    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue

        status_letter = parts[0].strip()
        # For renames (Rxxx), the path is the destination (parts[2])
        if status_letter.startswith("R"):
            path = parts[2] if len(parts) > 2 else parts[1]
            status = "modified"
        elif status_letter == "A":
            path = parts[1]
            status = "added"
        elif status_letter == "D":
            path = parts[1]
            status = "deleted"
        elif status_letter == "M":
            path = parts[1]
            status = "modified"
        elif status_letter == "T":
            # Type change (e.g. file → symlink)
            path = parts[1]
            status = "modified"
        elif status_letter == "C":
            # Copy
            path = parts[2] if len(parts) > 2 else parts[1]
            status = "added"
        else:
            path = parts[1]
            status = "modified"

        files.append({"path": path, "status": status})

    return files


def get_commit_files(git_directory: str, commit_hash: str) -> dict:
    """Return commit changed files as stats + file tree.

    Returns a dict with:
    - ``stats``: ``{modified, added, deleted}`` counts
    - ``tree``: file tree in the same format as the directory-tree API

    Raises GitError on failure.
    """
    files = _parse_commit_files(git_directory, commit_hash)

    # Use abbreviated hash (first 7 chars) for the root node name.
    short_hash = commit_hash[:7] if len(commit_hash) > 7 else commit_hash

    return {
        "stats": _compute_stats(files),
        "tree": _build_file_tree(files, root_name=f"Commit {short_hash}"),
    }


# ---------------------------------------------------------------------------
# Git log
# ---------------------------------------------------------------------------


def get_git_log(git_directory: str) -> dict:
    """Run ``git log`` and return parsed entries for the GitLog component.

    Args:
        git_directory: Absolute path to the root of the git repository.

    Returns:
        A dict with keys:
        - ``entries``: list of GitLogEntry dicts (max :data:`GIT_LOG_MAX_ENTRIES`).
        - ``has_more``: True if there are more commits beyond the limit.
        - ``head_commit_hash``: abbreviated hash of HEAD (or None).
        - ``index_files``: changed files info ``{stats, tree}`` or None.

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
        "index_files": get_index_files(git_directory),
    }


# ---------------------------------------------------------------------------
# File diff retrieval
# ---------------------------------------------------------------------------


def _git_show(git_directory: str, ref_and_path: str) -> tuple[str | None, bool]:
    """Run ``git show <ref>:<path>`` and return the file content.

    Args:
        git_directory: Absolute path to the git repository root.
        ref_and_path: A git ref + path expression, e.g. ``"HEAD:src/main.py"``
            or ``"abc1234^:src/main.py"``.

    Returns:
        A tuple ``(content, is_binary)``:
        - ``(content_str, False)`` for a normal text file.
        - ``(None, True)`` if the file is binary.
        - ``(None, False)`` if the file does not exist at the given ref
          or the git command fails for another reason.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "show", ref_and_path],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, False

    if result.returncode != 0:
        return None, False

    # Check size limit
    if len(result.stdout) > MAX_FILE_SIZE:
        return None, False

    # Try UTF-8 decode; binary files will fail
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None, True

    return content, False


def get_index_file_diff(git_directory: str, file_path: str) -> dict:
    """Return the original (HEAD) and modified (working tree) content of a file.

    Used to display a diff of uncommitted changes in the Monaco diff editor.

    Args:
        git_directory: Absolute path to the git repository root.
        file_path: File path relative to the git root.

    Returns:
        A dict with keys:
        - ``original``: file content at HEAD, or None if the file is new.
        - ``modified``: file content on disk, or None if the file was deleted.
        - ``binary``: True if either version is binary.
        - ``error``: error message string, or None.
    """
    # Original: content at HEAD
    original, original_binary = _git_show(git_directory, f"HEAD:{file_path}")

    if original_binary:
        return {"original": None, "modified": None, "binary": True, "error": None}

    # Modified: current content on disk
    abs_path = os.path.join(git_directory, file_path)

    if not os.path.isfile(abs_path):
        # File was deleted from disk (but existed in HEAD)
        return {"original": original, "modified": None, "binary": False, "error": None}

    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return {"original": None, "modified": None, "binary": False, "error": "Cannot read file"}

    if size > MAX_FILE_SIZE:
        return {"original": None, "modified": None, "binary": False, "error": "File too large"}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            modified = f.read()
    except UnicodeDecodeError:
        return {"original": None, "modified": None, "binary": True, "error": None}
    except OSError:
        return {"original": None, "modified": None, "binary": False, "error": "Cannot read file"}

    return {"original": original, "modified": modified, "binary": False, "error": None}


def get_commit_file_diff(git_directory: str, commit_hash: str, file_path: str) -> dict:
    """Return the original (parent) and modified (commit) content of a file.

    Used to display a diff of a specific commit in the Monaco diff editor.

    Args:
        git_directory: Absolute path to the git repository root.
        commit_hash: The commit hash to diff.
        file_path: File path relative to the git root.

    Returns:
        A dict with keys:
        - ``original``: file content at the parent commit, or None if the file
          was added in this commit (or it's a root commit).
        - ``modified``: file content at this commit, or None if the file was
          deleted in this commit.
        - ``binary``: True if either version is binary.
        - ``error``: error message string, or None.
    """
    # Original: content at parent commit
    original, original_binary = _git_show(git_directory, f"{commit_hash}^:{file_path}")

    if original_binary:
        return {"original": None, "modified": None, "binary": True, "error": None}

    # Modified: content at this commit
    modified, modified_binary = _git_show(git_directory, f"{commit_hash}:{file_path}")

    if modified_binary:
        return {"original": None, "modified": None, "binary": True, "error": None}

    return {"original": original, "modified": modified, "binary": False, "error": None}


class GitError(Exception):
    """Raised when a git operation fails."""
