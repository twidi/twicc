"""Git operations for the API.

Provides git log parsing for the GitLog visualization component,
and file diff retrieval for the Monaco diff editor.
"""

import base64
import os
import re
import signal
import subprocess
import threading
import time
from contextlib import suppress
from typing import NamedTuple

from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.file_content import IMAGE_EXTENSIONS, MAX_FILE_SIZE

# Maximum number of entries returned to the frontend.
GIT_LOG_MAX_ENTRIES = 200

# We fetch one extra to detect if there are more commits beyond the limit.
_GIT_LOG_FETCH_LIMIT = GIT_LOG_MAX_ENTRIES + 1

# ASCII Unit Separator — safe delimiter that won't appear in commit messages.
_FIELD_SEP = "\x1f"

# git log --pretty format using unit separator between fields.
# Fields: hash, parents, branch-ref, subject, committer-date, author-date,
#         author-name, author-email, decorations
_GIT_LOG_FORMAT = _FIELD_SEP.join(
    ["%h", "%p", "%S", "%s", "%cd", "%ad", "%an", "%ae", "%D"]
)

# Patterns to exclude from decoration lists.
# With ``--decorate=full`` the decorations use canonical ref paths.
_DECORATION_EXCLUDES = frozenset({"refs/stash"})
_DECORATION_EXCLUDE_SUFFIXES = ("/HEAD",)  # e.g. refs/remotes/origin/HEAD

# Timeout for the git subprocess (seconds).
_GIT_TIMEOUT = 10

# Dedicated timeout for ``git worktree add`` (seconds): it materializes a full
# checkout, which can take far longer than the read-only commands above.
_WORKTREE_ADD_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Failure messages
# ---------------------------------------------------------------------------


def _git_failed_message(text: str, key: str, stderr: str):
    """Build a failure message; the detail variant has its own catalog key (``<key>_detail``)."""
    if not stderr:
        return ui_message_text(text, key)
    return ui_message_text(f"{text}: {stderr}", f"{key}_detail", {"detail": stderr})


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
    _STATUS_KEYS = (
        "status", "staged_status", "unstaged_status", "conflicted", "conflict_code",
        "additions", "deletions",
    )

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
    """Compute {modified, added, deleted, conflicted} counts from a flat file list.

    Works with both commit files (``"status"`` key) and index files
    (``"staged_status"`` / ``"unstaged_status"`` keys).  For index files the
    stat reflects the *most significant* status of the file (staged wins over
    unstaged if both are present; among statuses: deleted > modified > added).
    """
    modified = 0
    added = 0
    deleted = 0
    conflicted = 0

    for f in files:
        if f.get("conflicted"):
            conflicted += 1
            continue
        # Commit files have "status", index files have "staged_status"/"unstaged_status".
        status = f.get("status") or f.get("staged_status") or f.get("unstaged_status")
        if status in ("modified", "renamed"):
            modified += 1
        elif status in ("added", "untracked"):
            added += 1
        elif status == "deleted":
            deleted += 1

    return {"modified": modified, "added": added, "deleted": deleted, "conflicted": conflicted}


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


def get_branches(git_directory: str) -> list[str]:
    """Return the list of local branch names, most recently committed first.

    The current branch (if any) is always first in the list.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "for-each-ref", "--sort=-committerdate",
             "--format=%(refname:short)", "refs/heads"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return []
        branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
        # Put current branch first
        current = get_current_branch(git_directory)
        if current and current in branches:
            branches.remove(current)
            branches.insert(0, current)
        return branches
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_commit_timestamp(git_directory: str, rev: str) -> int:
    """Return the committer timestamp (Unix seconds) of ``rev``, or 0 if unknown."""
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "log", "-1", "--format=%ct", rev, "--"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return 0
        return int(result.stdout.strip() or 0)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return 0


class WorktreeInfo(NamedTuple):
    """One entry from ``git worktree list --porcelain``.

    ``path`` is the absolute worktree directory as git reports it. ``branch``
    is the short branch name checked out there, or ``None`` for a detached
    HEAD or a bare main repo. ``head`` is the commit sha (``None`` only for a
    bare entry). The remaining flags mirror the porcelain attributes; their
    reason strings are ``""`` when git emits the flag without a reason.
    """

    path: str
    head: str | None
    branch: str | None
    is_bare: bool
    is_detached: bool
    is_locked: bool
    locked_reason: str
    is_prunable: bool
    prunable_reason: str


def list_worktrees(git_directory: str) -> list[WorktreeInfo]:
    """Return every worktree of the repository containing ``git_directory``.

    Parses the full ``git worktree list --porcelain`` output — which works
    from the main checkout or from any linked worktree, and always lists the
    main checkout first. Returns ``[]`` if git is unavailable or the command
    fails. Entries are blank-line separated in the porcelain format.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []

    worktrees: list[WorktreeInfo] = []
    cur: dict | None = None

    def flush() -> None:
        if cur is None or "path" not in cur:
            return
        worktrees.append(WorktreeInfo(
            path=cur["path"],
            head=cur.get("head"),
            branch=cur.get("branch"),
            is_bare=cur.get("bare", False),
            is_detached=cur.get("detached", False),
            is_locked=cur.get("locked", False),
            locked_reason=cur.get("locked_reason", ""),
            is_prunable=cur.get("prunable", False),
            prunable_reason=cur.get("prunable_reason", ""),
        ))

    for line in result.stdout.splitlines():
        if not line:
            flush()
            cur = None
            continue
        if cur is None:
            cur = {}
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch refs/heads/"):
            cur["branch"] = line[len("branch refs/heads/"):]
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            cur["locked"] = True
            cur["locked_reason"] = line[len("locked "):] if line.startswith("locked ") else ""
        elif line == "prunable" or line.startswith("prunable "):
            cur["prunable"] = True
            cur["prunable_reason"] = line[len("prunable "):] if line.startswith("prunable ") else ""
    flush()
    return worktrees


def get_worktree_branches(git_directory: str) -> set[str]:
    """Return the set of local branch names currently checked out in any
    worktree of the repo (including the main checkout).

    Detached-HEAD (and bare) worktrees have no branch and are skipped.
    """
    return {wt.branch for wt in list_worktrees(git_directory) if wt.branch}


def create_worktree(
    repo_root: str,
    path: str,
    branch: str,
    start_point: str | None = None,
) -> tuple[bool, str]:
    """Create a git worktree at ``path``.

    If ``branch`` is an existing local branch it is checked out into the new
    worktree (``start_point`` is ignored); otherwise the branch is created
    with ``-b``, from ``start_point`` when given, else from the repo's HEAD.

    Returns ``(True, "")`` on success, ``(False, <git error>)`` on failure.
    Git's own stderr is relayed verbatim — git remains the authority on
    git-level validation (branch already checked out, non-empty target, ...).
    """
    if branch in get_branches(repo_root):
        args = ["git", "-C", repo_root, "worktree", "add", path, branch]
    else:
        args = ["git", "-C", repo_root, "worktree", "add", path, "-b", branch]
        if start_point:
            args.append(start_point)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_WORKTREE_ADD_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or ui_message_text("git worktree add failed", "backend.git.worktree_add_failed")
    return True, ""


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------


def _parse_decorations(raw: str) -> list[str]:
    """Parse the ``%D`` decoration string into a filtered list of full ref names.

    With ``--decorate=full`` the output already uses canonical ref paths::

        HEAD -> refs/heads/main, refs/remotes/origin/main, refs/remotes/origin/HEAD

    We drop ``HEAD -> …`` pointers and any ref ending with ``/HEAD`` (symbolic
    remote HEAD aliases like ``refs/remotes/origin/HEAD``).
    """
    if not raw or not raw.strip():
        return []

    refs: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        # Skip "HEAD -> refs/heads/…" (the symbolic HEAD pointer)
        if part.startswith("HEAD -> "):
            continue

        # Skip exact matches
        if part in _DECORATION_EXCLUDES:
            continue

        # Skip refs ending with /HEAD (e.g. refs/remotes/origin/HEAD)
        if any(part.endswith(suffix) for suffix in _DECORATION_EXCLUDE_SUFFIXES):
            continue

        refs.append(part)
    return refs


def _parse_git_log_line(line: str) -> dict | None:
    """Parse a single line of git log output into a GitLogEntry dict.

    Returns None if the line cannot be parsed (malformed or empty).
    """
    parts = line.split(_FIELD_SEP)
    if len(parts) != 9:
        return None

    (
        hash_,
        parents_str,
        branch,
        message,
        committer_date,
        author_date,
        author_name,
        author_email,
        decorations_raw,
    ) = parts

    parents = parents_str.split() if parents_str.strip() else []

    entry = {
        "hash": hash_,
        "branch": branch,
        "parents": parents,
        "message": message,
        "committerDate": committer_date.strip(),
    }

    # Decorations: all refs pointing at this commit (filtered).
    decorations = _parse_decorations(decorations_raw)
    if decorations:
        entry["decorations"] = decorations

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
# Single commit detail
# ---------------------------------------------------------------------------


def get_commit_detail(git_directory: str, commit_hash: str) -> dict:
    """Return detailed information for a single commit.

    Uses ``git show --no-patch`` with a format that includes the body,
    author info, and committer info.

    Returns::

        {
            "hash": "abc1234",
            "message": "feat: ...",
            "body": "Optional multi-line body...",
            "committerDate": "2026-01-05 09:00:00 +0200",
            "authorDate": "2026-01-05 09:00:00 +0200",
            "author": { "name": "...", "email": "..." },
            "committer": { "name": "...", "email": "..." },
        }

    Raises:
        GitError: If the git command fails.
    """
    # Body (%b) is last because it can span multiple lines.
    # We use %x00 (NUL) as a sentinel after the fixed fields,
    # so everything after the NUL is the body.
    fmt = _FIELD_SEP.join(["%h", "%s", "%cd", "%ad", "%an", "%ae", "%cn", "%ce"]) + "%x00%b"

    try:
        result = subprocess.run(
            [
                "git", "-C", git_directory,
                "show", "--no-patch", f"--pretty=format:{fmt}", "--date=iso",
                commit_hash,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(ui_message_text("Git show timed out", 'backend.git.git_show_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("Git show failed", 'backend.git.git_show_failed', stderr))

    stdout = result.stdout
    # Split on NUL: fixed fields before, body after.
    nul_pos = stdout.find("\0")
    if nul_pos == -1:
        raise GitError(ui_message_text("Unexpected git show output format", 'backend.git.unexpected_show_output'))

    fixed_part = stdout[:nul_pos]
    body_raw = stdout[nul_pos + 1:]

    parts = fixed_part.split(_FIELD_SEP)
    if len(parts) != 8:
        raise GitError(ui_message_text("Unexpected git show output format", 'backend.git.unexpected_show_output'))

    hash_, message, committer_date, author_date, author_name, author_email, committer_name, committer_email = parts

    entry: dict = {
        "hash": hash_,
        "message": message,
        "committerDate": committer_date.strip(),
    }

    if author_date.strip():
        entry["authorDate"] = author_date.strip()

    # Author info.
    a_name = author_name.strip() or None
    a_email = author_email.strip() or None
    if a_name or a_email:
        entry["author"] = {k: v for k, v in [("name", a_name), ("email", a_email)] if v}

    # Committer info (always included, even if same as author — the frontend decides what to show).
    c_name = committer_name.strip() or None
    c_email = committer_email.strip() or None
    if c_name or c_email:
        entry["committer"] = {k: v for k, v in [("name", c_name), ("email", c_email)] if v}

    body = body_raw.strip()
    if body:
        entry["body"] = body

    return entry


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
            "unstaged_status": "modified"|"added"|"deleted"|"untracked"|None,
            # conflicted entries only:
            "conflicted": True,
            "conflict_code": "UU",   # the raw XY porcelain code
        }

    The ``git status --porcelain`` format is ``XY path`` where:
    - X = index (staged) status
    - Y = worktree (unstaged) status

    A file can be both staged and unstaged (e.g. partially staged changes).
    Untracked files (``??``) are reported with ``unstaged_status="untracked"``.
    Unmerged/conflicted entries are reported with ``conflicted=True`` and the
    raw two-letter ``conflict_code`` (staged/unstaged left None).

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

        # Unmerged (conflicted) entries. In porcelain v1 these are the codes
        # DD, AU, UD, UA, DU, AA, UU — i.e. either side is "U", or both sides
        # are an identical add/delete. They don't map to a normal staged/unstaged
        # change, so surface them explicitly (with the raw XY code) instead of
        # dropping them — "U" otherwise normalizes to None and the file vanishes.
        if x == "U" or y == "U" or (x == y and x in ("A", "D")):
            files.append({
                "path": path,
                "staged_status": None,
                "unstaged_status": None,
                "conflicted": True,
                "conflict_code": x + y,
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


# Untracked files larger than this are not line-counted (avoids reading huge
# blobs on every live poll); they simply carry no +/- stat.
_NUMSTAT_UNTRACKED_MAX_BYTES = 1_000_000


def _parse_numstat_z(stdout: str) -> dict[str, tuple[int | None, int | None]]:
    """Parse NUL-terminated ``--numstat -z`` output into ``path -> (add, del)``.

    Binary files map to ``(None, None)``. A rename has an empty path field
    followed by two extra NUL fields (old, new); it is keyed by the new path.
    """
    stats: dict[str, tuple[int | None, int | None]] = {}
    tokens = stdout.split("\x00")
    i = 0
    while i < len(tokens):
        rec = tokens[i]
        if not rec:
            i += 1
            continue
        parts = rec.split("\t", 2)
        if len(parts) < 3:
            i += 1
            continue
        add_s, del_s, path = parts
        if path == "":
            # Rename: use the new path (second of the two trailing fields).
            path = tokens[i + 2] if i + 2 < len(tokens) else ""
            i += 3
        else:
            i += 1
        if not path:
            continue
        adds = None if add_s == "-" else int(add_s)
        dels = None if del_s == "-" else int(del_s)
        stats[path] = (adds, dels)
    return stats


def _get_numstat_vs_head(git_directory: str) -> dict[str, tuple[int | None, int | None]]:
    """``path -> (additions, deletions)`` for tracked changes vs ``HEAD``.

    A single ``git diff HEAD --numstat -z`` — combines staged + unstaged changes
    (the file's total diff against the last commit). Returns ``{}`` on failure
    (e.g. a repo with no commits yet).
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "diff", "HEAD", "--numstat", "-z"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    return _parse_numstat_z(result.stdout)


def _get_commit_numstat(git_directory: str, commit_hash: str) -> dict[str, tuple[int | None, int | None]]:
    """``path -> (additions, deletions)`` for the files changed by a commit."""
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "diff-tree", "--no-commit-id",
             "--numstat", "--root", "-r", "-z", commit_hash],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    return _parse_numstat_z(result.stdout)


def _count_untracked_lines(git_directory: str, path: str) -> int | None:
    """Line count of an untracked file (all additions). None if binary/too large."""
    full = os.path.join(git_directory, path)
    try:
        if os.path.getsize(full) > _NUMSTAT_UNTRACKED_MAX_BYTES:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\x00" in data:
        return None  # binary
    if not data:
        return 0
    count = data.count(b"\n")
    if not data.endswith(b"\n"):
        count += 1
    return count


def _attach_line_stats(files: list[dict], git_directory: str) -> None:
    """Annotate each (non-conflict) index file with ``additions``/``deletions``."""
    numstat = _get_numstat_vs_head(git_directory)
    for f in files:
        if f.get("conflicted"):
            continue
        path = f["path"]
        if f.get("unstaged_status") == "untracked" and not f.get("staged_status"):
            adds = _count_untracked_lines(git_directory, path)
            dels = 0 if adds is not None else None
        else:
            adds, dels = numstat.get(path, (None, None))
        if adds is not None:
            f["additions"] = adds
        if dels is not None:
            f["deletions"] = dels


def get_index_files(git_directory: str) -> dict | None:
    """Return working-tree changed files as stats + file tree.

    Returns a dict with:
    - ``stats``: ``{modified, added, deleted, conflicted}`` counts
    - ``tree``: file tree in the same format as the directory-tree API, each file
      node also carrying ``additions``/``deletions`` line counts where known

    Returns None if there are no changes.
    """
    files = _parse_index_files(git_directory)
    if not files:
        return None

    _attach_line_stats(files, git_directory)

    return {
        "stats": _compute_stats(files),
        "tree": {
            **_build_file_tree(files, root_name="Uncommitted files"),
            "name_ui_message": ui_message("backend.git.uncommitted_files"),
        },
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
        raise GitError(ui_message_text("Git diff-tree timed out", 'backend.git.git_diff-tree_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("Git diff-tree failed", 'backend.git.git_diff-tree_failed', stderr))

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
    - ``stats``: ``{modified, added, deleted, conflicted}`` counts
    - ``tree``: file tree in the same format as the directory-tree API, each file
      node also carrying ``additions``/``deletions`` line counts where known

    Raises GitError on failure.
    """
    files = _parse_commit_files(git_directory, commit_hash)

    numstat = _get_commit_numstat(git_directory, commit_hash)
    for f in files:
        adds, dels = numstat.get(f["path"], (None, None))
        if adds is not None:
            f["additions"] = adds
        if dels is not None:
            f["deletions"] = dels

    # Use abbreviated hash (first 7 chars) for the root node name.
    short_hash = commit_hash[:7] if len(commit_hash) > 7 else commit_hash

    return {
        "stats": _compute_stats(files),
        "tree": {
            **_build_file_tree(files, root_name=f"Commit {short_hash}"),
            "name_ui_message": ui_message("backend.git.commit_files", {"hash": short_hash}),
        },
    }


# ---------------------------------------------------------------------------
# Git log
# ---------------------------------------------------------------------------


def get_git_log(git_directory: str, branch: str | None = None) -> dict:
    """Run ``git log`` and return parsed entries for the GitLog component.

    Args:
        git_directory: Absolute path to the root of the git repository.
        branch: Optional branch name to filter commits. If None or empty,
            ``--all`` is used to show commits from all branches.

    Returns:
        A dict with keys:
        - ``entries``: list of GitLogEntry dicts (max :data:`GIT_LOG_MAX_ENTRIES`).
        - ``has_more``: True if there are more commits beyond the limit.
        - ``head_commit_hash``: abbreviated hash of HEAD (or None).
        - ``index_files``: changed files info ``{stats, tree}`` or None.
        - ``branches``: list of local branch names.

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
    ]
    if branch:
        cmd.append(branch)
    else:
        cmd.append("--all")
    cmd += [
        f"-{_GIT_LOG_FETCH_LIMIT}",
        f"--pretty=format:{_GIT_LOG_FORMAT}",
        "--date=iso",
        "--decorate=full",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(ui_message_text("Git log timed out", 'backend.git.git_log_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("Git log failed", 'backend.git.git_log_failed', stderr))

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
        "branches": get_branches(git_directory),
    }


# ---------------------------------------------------------------------------
# File diff retrieval
# ---------------------------------------------------------------------------


def _git_show(git_directory: str, ref_and_path: str) -> tuple[str | None, bool, bytes | None]:
    """Run ``git show <ref>:<path>`` and return the file content.

    Args:
        git_directory: Absolute path to the git repository root.
        ref_and_path: A git ref + path expression, e.g. ``"HEAD:src/main.py"``
            or ``"abc1234^:src/main.py"``.

    Returns:
        A tuple ``(content, is_binary, raw_bytes)``:
        - ``(content_str, False, None)`` for a normal text file.
        - ``(None, True, raw_bytes)`` if the file is binary.
        - ``(None, False, None)`` if the file does not exist at the given ref
          or the git command fails for another reason.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "show", ref_and_path],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, False, None

    if result.returncode != 0:
        return None, False, None

    # Check size limit
    if len(result.stdout) > MAX_FILE_SIZE:
        return None, False, None

    # Try UTF-8 decode; binary files will fail
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None, True, result.stdout

    return content, False, None


def _make_image_data_uri(file_path: str, raw_bytes: bytes) -> str | None:
    """Create a data URI for an image file from raw bytes, or None if not an image."""
    ext = os.path.splitext(file_path)[1].lower()
    mime_type = IMAGE_EXTENSIONS.get(ext)
    if not mime_type:
        return None
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _read_image_from_disk(file_path: str) -> str | None:
    """Read an image file from disk and return a data URI, or None."""
    ext = os.path.splitext(file_path)[1].lower()
    mime_type = IMAGE_EXTENSIONS.get(ext)
    if not mime_type:
        return None
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def get_index_file_diff(git_directory: str, file_path: str) -> dict:
    """Return the original (HEAD) and modified (working tree) content of a file.

    Used to display a diff of uncommitted changes in the Monaco diff editor.

    Args:
        git_directory: Absolute path to the git repository root.
        file_path: File path relative to the git root.

    Returns:
        A dict with keys:
        - ``original``: file content at HEAD (or data URI for images), or None if the file is new.
        - ``modified``: file content on disk (or data URI for images), or None if the file was deleted.
        - ``binary``: True if either version is binary.
        - ``image``: True if the file is a known image type (only present when binary).
        - ``error``: error message string, or None.
    """
    # Original: content at HEAD
    original, original_binary, original_bytes = _git_show(git_directory, f"HEAD:{file_path}")

    if original_binary:
        original_image = _make_image_data_uri(file_path, original_bytes) if original_bytes else None
        if not original_image:
            return {"original": None, "modified": None, "binary": True, "error": None}
        # Original is an image — check modified on disk too
        abs_path = os.path.join(git_directory, file_path)
        modified_image = _read_image_from_disk(abs_path) if os.path.isfile(abs_path) else None
        return {"original": original_image, "modified": modified_image, "binary": True, "image": True, "error": None}

    # Modified: current content on disk
    abs_path = os.path.join(git_directory, file_path)

    if not os.path.isfile(abs_path):
        # File was deleted from disk (but existed in HEAD)
        return {"original": original, "modified": None, "binary": False, "error": None}

    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return {"original": None, "modified": None, "binary": False, "error": ui_message_text("Cannot read file", 'backend.git.read_failed')}

    if size > MAX_FILE_SIZE:
        return {"original": None, "modified": None, "binary": False, "error": ui_message_text("File too large", 'backend.git.file_too_large'), "too_large": True}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            modified = f.read()
    except UnicodeDecodeError:
        # Modified is binary — check if it's an image (new image or text replaced by image)
        modified_image = _read_image_from_disk(abs_path)
        if modified_image:
            return {"original": None, "modified": modified_image, "binary": True, "image": True, "error": None}
        return {"original": None, "modified": None, "binary": True, "error": None}
    except OSError:
        return {"original": None, "modified": None, "binary": False, "error": ui_message_text("Cannot read file", 'backend.git.read_failed')}

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
        - ``original``: file content at the parent commit (or data URI for images),
          or None if the file was added in this commit (or it's a root commit).
        - ``modified``: file content at this commit (or data URI for images),
          or None if the file was deleted in this commit.
        - ``binary``: True if either version is binary.
        - ``image``: True if the file is a known image type (only present when binary).
        - ``error``: error message string, or None.
    """
    # Original: content at parent commit
    original, original_binary, original_bytes = _git_show(git_directory, f"{commit_hash}^:{file_path}")

    # Modified: content at this commit
    modified, modified_binary, modified_bytes = _git_show(git_directory, f"{commit_hash}:{file_path}")

    if original_binary or modified_binary:
        original_image = _make_image_data_uri(file_path, original_bytes) if original_bytes else None
        modified_image = _make_image_data_uri(file_path, modified_bytes) if modified_bytes else None
        if original_image or modified_image:
            return {"original": original_image, "modified": modified_image, "binary": True, "image": True, "error": None}
        return {"original": None, "modified": None, "binary": True, "error": None}

    return {"original": original, "modified": modified, "binary": False, "error": None}


# ---------------------------------------------------------------------------
# Git working-tree actions (stage, unstage, discard)
# ---------------------------------------------------------------------------


def validate_path_in_repo(git_directory: str, file_path: str) -> str:
    """Validate that file_path stays within git_directory. Returns absolute path.

    Raises GitError if the resolved path escapes the repository root.
    """
    abs_path = os.path.normpath(os.path.join(git_directory, file_path))
    if not abs_path.startswith(os.path.normpath(git_directory) + os.sep) and abs_path != os.path.normpath(git_directory):
        raise GitError(ui_message_text("Path is outside the repository", 'backend.git.path_outside_repository'))
    return abs_path


def git_stage(git_directory: str, file_path: str) -> None:
    """Stage a file (``git add <path>``).

    Raises GitError on failure.
    """
    validate_path_in_repo(git_directory, file_path)

    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "add", "--", file_path],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(ui_message_text("git add timed out", 'backend.git.git_add_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("git add failed", 'backend.git.git_add_failed', stderr))


def git_unstage(git_directory: str, file_path: str) -> None:
    """Unstage a file (``git restore --staged <path>``).

    Raises GitError on failure.
    """
    validate_path_in_repo(git_directory, file_path)

    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "restore", "--staged", "--", file_path],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(ui_message_text("git restore --staged timed out", 'backend.git.git_restore_staged_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("git restore --staged failed", 'backend.git.git_restore_staged_failed', stderr))


def git_discard(git_directory: str, file_path: str) -> None:
    """Discard unstaged changes for a tracked file (``git restore <path>``).

    For deleted files this restores the file from HEAD.

    Raises GitError on failure.
    """
    validate_path_in_repo(git_directory, file_path)

    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "restore", "--", file_path],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(ui_message_text("git restore timed out", 'backend.git.git_restore_timeout'))
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(_git_failed_message("git restore failed", 'backend.git.git_restore_failed', stderr))


# ---------------------------------------------------------------------------
# Git file downloads (streamed — no size cap)
# ---------------------------------------------------------------------------
# The Git tab's context menu downloads a file's content at a given ref, or the
# unified patch for it. Unlike the diff endpoints (which buffer into JSON and
# cap at MAX_FILE_SIZE for the editor), these stream git's stdout straight into
# the HTTP response, so a large blob never lands in memory.

# A ref accepted by the download endpoints: ``index`` or an abbreviated/full
# commit hash. Anything else is refused — the value is interpolated into a
# revision spec, so it must never carry an option or an arbitrary expression.
_COMMIT_REF_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")

# Bytes read per chunk when streaming a git process' stdout.
_STREAM_CHUNK_SIZE = 64 * 1024
_STREAM_TERMINATE_GRACE = 1


def is_valid_commit_ref(ref: str) -> bool:
    """Whether ``ref`` is a hash the download endpoints may interpolate."""
    return bool(_COMMIT_REF_RE.match(ref or ""))


class GitProcessStream:
    """Own stdout, completion validation, and interrupted process shutdown."""

    def __init__(self, proc: subprocess.Popen, *, accepted_returncodes=(0,), owned_process_group: int | None = None):
        self._proc = proc
        self.reader = proc.stdout
        self._accepted_returncodes = accepted_returncodes
        # Only the spawn path that creates a dedicated session supplies this.
        # Generic callers own their child, never its inherited process group.
        self._owned_process_group = owned_process_group
        self._closing = threading.Event()
        self._close_lock = threading.Lock()

    def read(self, size: int = -1) -> bytes:
        return self.reader.read(size)

    def finish(self) -> None:
        """Validate natural exit after stdout reaches EOF, without a close lock."""
        returncode = self._proc.wait()
        if returncode not in self._accepted_returncodes and not self._closing.is_set():
            # This error aborts the body; headers have already been sent.
            raise GitError(f"Git download process exited with status {returncode}")

    def close(self) -> None:
        # Never hold this lock in finish/read: shutdown must interrupt both.
        with self._close_lock:
            if self._closing.is_set():
                return
            self._closing.set()
            if self._owned_process_group is not None:
                self._stop_process_group()
            elif self._proc.poll() is None:
                with suppress(ProcessLookupError):
                    self._proc.terminate()
                try:
                    self._proc.wait(timeout=_STREAM_TERMINATE_GRACE)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        self._proc.kill()
                    self._proc.wait()
            else:
                self._proc.wait()
            # BufferedReader.close waits for a read's lock. Stop all owned
            # stdout producers first, including helpers left after Git exits.
            self.reader.close()

    def _stop_process_group(self) -> None:
        """Stop the dedicated session's producers, even after its leader exits."""
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(self._owned_process_group, signal.SIGTERM)
        deadline = time.monotonic() + _STREAM_TERMINATE_GRACE
        while True:
            # Reap the direct child while checking for surviving helpers.
            # finish() may also wait, without acquiring the close lock.
            self._proc.poll()
            try:
                os.killpg(self._owned_process_group, 0)
            except (ProcessLookupError, PermissionError):
                # macOS can report PermissionError for a group of zombies.
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(self._owned_process_group, signal.SIGKILL)
                break
            time.sleep(min(0.01, remaining))
        self._proc.wait()


def _spawn_git(git_directory: str, args: list[str], *, accepted_returncodes=(0,)) -> GitProcessStream:
    """Start ``git -C <dir> <args>`` and return its stdout as a stream."""
    try:
        proc = subprocess.Popen(
            ["git", "-C", git_directory, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=_STREAM_CHUNK_SIZE,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise GitError(ui_message_text("Git is not installed or not in PATH", 'backend.git.not_installed'))
    return GitProcessStream(proc, accepted_returncodes=accepted_returncodes, owned_process_group=proc.pid)


def blob_exists_at_ref(git_directory: str, ref: str, file_path: str) -> bool:
    """Whether ``<ref>:<file_path>`` resolves to a blob (a regular file).

    ``cat-file --batch-check`` answers in one call and stays quiet on a missing
    object (it prints ``<spec> missing`` and still exits 0), so a lookup for a
    path that never existed is not an error.
    """
    validate_path_in_repo(git_directory, file_path)

    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "cat-file", "--batch-check"],
            input=f"{ref}:{file_path}\n",
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    if result.returncode != 0:
        return False
    parts = result.stdout.split()
    return len(parts) >= 2 and parts[1] == "blob"


def stream_blob_at_ref(git_directory: str, ref: str, file_path: str) -> GitProcessStream:
    """Stream the content of ``<ref>:<file_path>``.

    The caller checks :func:`blob_exists_at_ref` first — ``cat-file blob`` on a
    missing object would only fail once the stream is already open.
    """
    validate_path_in_repo(git_directory, file_path)
    return _spawn_git(git_directory, ["cat-file", "blob", f"{ref}:{file_path}"])


def is_tracked(git_directory: str, file_path: str) -> bool:
    """Whether the file is known to the index (i.e. not untracked)."""
    validate_path_in_repo(git_directory, file_path)

    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "ls-files", "--error-unmatch", "--", file_path],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def stream_index_patch(git_directory: str, file_path: str) -> GitProcessStream:
    """Stream the unified patch of a working-tree file against HEAD.

    An untracked file has nothing to diff against, so it is compared to
    ``/dev/null`` instead (``--no-index`` exits 1 when the files differ, which
    is the normal case here and is accepted alongside zero).
    """
    validate_path_in_repo(git_directory, file_path)

    if is_tracked(git_directory, file_path):
        return _spawn_git(git_directory, ["diff", "HEAD", "--", file_path])
    return _spawn_git(
        git_directory, ["diff", "--no-index", "--", os.devnull, file_path],
        accepted_returncodes=(0, 1),
    )


def stream_commit_patch(git_directory: str, commit_hash: str, file_path: str) -> GitProcessStream:
    """Stream the unified patch a commit applied to one file.

    ``--root`` covers the initial commit (no parent). Merge commits show no
    patch, which matches what the diff view already displays for them.
    """
    validate_path_in_repo(git_directory, file_path)
    return _spawn_git(
        git_directory,
        ["show", "--format=", "--root", commit_hash, "--", file_path],
    )


# ---------------------------------------------------------------------------
# Filesystem .git resolution (no subprocess)
# ---------------------------------------------------------------------------
# These helpers walk up from a directory to find a ``.git`` folder/file and
# read its ``HEAD`` directly, instead of shelling out to ``git``. They are
# called from per-item compute paths where invoking ``git`` per call would
# dominate the cost. Filesystem-only and provider-agnostic.


# Module-level cache for batch compute: directory path → (git_directory, git_branch) or None
_git_resolution_cache: dict[str, tuple[str, str] | None] = {}


def resolve_git_from_path(dir_path: str, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Walk up from dir_path to find a .git entry and resolve git directory and branch.

    Args:
        dir_path: An absolute directory path to start from
        use_cache: Whether to read from and write to the module-level resolution cache.
                   Set to False for live resolution where fresh results are needed.

    Returns:
        (git_directory, git_branch) tuple, or None if no .git found
    """
    traversed: list[str] = []
    current = dir_path

    while True:
        # Check cache for this directory
        if use_cache and current in _git_resolution_cache:
            result = _git_resolution_cache[current]
            # Cache all traversed intermediate paths
            for path in traversed:
                _git_resolution_cache[path] = result
            return result

        traversed.append(current)

        git_path = os.path.join(current, '.git')
        try:
            if os.path.isdir(git_path):
                # Main repo: .git is a directory
                branch = read_head_branch(os.path.join(git_path, 'HEAD'))
                result = (current, branch) if branch is not None else None
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

            elif os.path.isfile(git_path):
                # Worktree: .git is a file containing "gitdir: /path/to/.git/worktrees/name"
                result = _resolve_worktree_git(current, git_path)
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

        except OSError:
            # Permission error or other OS issue, skip this level
            pass

        # Move up one directory
        parent = os.path.dirname(current)
        if parent == current:
            # Reached filesystem root without finding .git
            if use_cache:
                for path in traversed:
                    _git_resolution_cache[path] = None
            return None
        current = parent


def _resolve_worktree_git(git_directory: str, git_file_path: str) -> tuple[str, str] | None:
    """
    Resolve git branch from a worktree's .git file.

    The .git file contains something like:
        gitdir: /home/user/project/.git/worktrees/worktree-name

    The HEAD file in that gitdir path contains the branch reference.

    Args:
        git_directory: The directory containing the .git file (the worktree root)
        git_file_path: Path to the .git file

    Returns:
        (git_directory, git_branch) tuple, or None on error
    """
    try:
        with open(git_file_path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return None

    if not content.startswith('gitdir: '):
        return None

    gitdir = content[len('gitdir: '):]
    head_path = os.path.join(gitdir, 'HEAD')
    branch = read_head_branch(head_path)
    if branch is None:
        return None
    return (git_directory, branch)


def read_head_branch(head_path: str) -> str | None:
    """
    Read a git HEAD file and extract the branch name or commit hash.

    HEAD contains either:
    - "ref: refs/heads/feature/upload" → branch = "feature/upload"
    - A raw commit hash (40 hex chars) → detached HEAD, return the hash

    Args:
        head_path: Path to the HEAD file

    Returns:
        Branch name or commit hash, or None on error
    """
    try:
        with open(head_path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return None

    if content.startswith('ref: refs/heads/'):
        return content[len('ref: refs/heads/'):]
    elif content.startswith('ref: '):
        # Other ref (unlikely but handle it)
        return content[len('ref: '):]
    elif len(content) >= 7 and all(c in '0123456789abcdef' for c in content):
        # Detached HEAD: raw commit hash
        return content
    return None


def resolve_worktree_main_repo(directory: str) -> str | None:
    """If *directory* lives inside a linked git worktree, return the absolute
    path of the worktree's MAIN repository root; otherwise return None.

    A linked worktree's ``.git`` is a *file* (not a directory) reading
    ``gitdir: <root>/.git/worktrees/<name>``. The main repository root is the
    directory three levels above that gitdir, but only when the layout is
    exactly ``<root>/.git/worktrees/<name>`` — verified by path segments so
    other ``.git``-file shapes are rejected: submodules (gitdir under
    ``.git/modules/``) and separate-git-dir repos (gitdir outside
    ``<root>/.git``) both yield None instead of pointing at the wrong repo.
    Walks up from *directory* to find the ``.git`` entry. Filesystem-only —
    never shells out to ``git``. Returns None for an ordinary repository
    (``.git`` is a directory) and for any layout it cannot safely interpret.
    """
    current = directory
    while True:
        git_path = os.path.join(current, '.git')
        try:
            if os.path.isdir(git_path):
                # Ordinary repository, not a worktree.
                return None
            if os.path.isfile(git_path):
                return _main_repo_from_worktree_gitfile(git_path)
        except OSError:
            return None

        parent = os.path.dirname(current)
        if parent == current:
            # Reached the filesystem root without finding a .git entry.
            return None
        current = parent


def _main_repo_from_worktree_gitfile(git_file_path: str) -> str | None:
    """Resolve the main repository root from a worktree's ``.git`` file.

    Accepts only the standard linked-worktree layout
    ``<root>/.git/worktrees/<name>`` (checked by path segments), so a submodule
    (gitdir under ``.git/modules/<name>``) or a separate-git-dir repo (gitdir
    outside ``<root>/.git``) is rejected with None rather than mistaken for a
    worktree of the wrong repository. The returned root is verified to exist.
    """
    try:
        with open(git_file_path) as f:
            content = f.read().strip()
    except OSError:
        return None

    if not content.startswith('gitdir: '):
        return None
    gitdir = content[len('gitdir: '):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(os.path.dirname(git_file_path), gitdir)
    gitdir = os.path.normpath(gitdir)

    # Standard linked-worktree layout only: <root>/.git/worktrees/<name>.
    # A submodule's gitdir is <super>/.git/modules/<name> (segment "modules"),
    # so requiring "worktrees" then ".git" excludes it cleanly; a relocated
    # (separate-git-dir) common dir won't be named ".git" and is rejected too.
    worktrees_dir = os.path.dirname(gitdir)        # expected: <root>/.git/worktrees
    common_git = os.path.dirname(worktrees_dir)    # expected: <root>/.git
    if os.path.basename(worktrees_dir) != 'worktrees' or os.path.basename(common_git) != '.git':
        return None

    root = os.path.dirname(common_git)             # <root>
    # Guard against a stale/corrupt pointer: require the gitdir itself to still
    # exist (a ``.git`` file left behind after ``git worktree prune`` would
    # otherwise still resolve a parent), and never link to a vanished repo.
    if not root or not os.path.isdir(gitdir) or not os.path.isdir(common_git) or not os.path.isdir(root):
        return None
    return root


def get_git_common_dir(git_root: str) -> str | None:
    """Return the realpath of the COMMON git directory for *git_root*.

    For an ordinary repository (``.git`` is a directory) this is the ``.git``
    directory itself. For a linked worktree (``.git`` is a file pointing at
    ``<root>/.git/worktrees/<name>``) this is the main repository's ``.git``.
    Two git roots sharing the same common dir belong to the same repository
    (main checkout + its worktrees). Returns None when there is no ``.git``
    entry or the layout cannot be safely interpreted.
    """
    git_path = os.path.join(git_root, '.git')
    try:
        if os.path.isdir(git_path):
            return os.path.realpath(git_path)
        if os.path.isfile(git_path):
            main_root = _main_repo_from_worktree_gitfile(git_path)
            if main_root is not None:
                return os.path.realpath(os.path.join(main_root, '.git'))
    except OSError:
        pass
    return None


def _is_path_within(path: str, base: str) -> bool:
    """True when *path* equals *base* or lives underneath it (both pre-normalized)."""
    return path == base or path.startswith(base + os.sep)


def is_git_root_related(git_root: str, anchor_dir: str, *, use_cache: bool = True) -> bool:
    """Whether *git_root* is a plausible git root for a session anchored at *anchor_dir*.

    Guards session git resolution against unrelated repositories: a session
    launched in ``/home/user/foo`` that merely reads a file under
    ``/home/user/bar`` (a separate repo) must not adopt ``bar`` as its git
    directory. A candidate is accepted when:

    - it is the anchor itself, one of its ancestors (the normal walk-up
      case: the anchor lives inside the candidate repo), or one of its
      descendants (a worktree created inside the anchor directory), or
    - it shares its common git directory with the repository containing the
      anchor — a worktree of the anchor's repo checked out elsewhere
      (e.g. ``/tmp/toto`` for a repo in ``/home/user/foo``).

    ``use_cache`` is forwarded to :func:`resolve_git_from_path` when
    resolving the anchor's own repository.
    """
    root = os.path.normpath(git_root)
    anchor = os.path.normpath(anchor_dir)
    if _is_path_within(anchor, root) or _is_path_within(root, anchor):
        return True

    anchor_git = resolve_git_from_path(anchor, use_cache=use_cache)
    if anchor_git is None:
        return False
    anchor_common = get_git_common_dir(anchor_git[0])
    if anchor_common is None:
        return False
    return get_git_common_dir(root) == anchor_common


# ---------------------------------------------------------------------------
# Worktree detection from a path marker (deleted-worktree backfill)
# ---------------------------------------------------------------------------
# When a worktree directory is gone AND git has pruned its admin record, the
# only thing left is the path string. A path *segment* that names a worktree
# container (``.worktrees`` and friends) or a directly-named ``worktree-*``
# folder is a strong signal that the project was a worktree — strong enough to
# tell it apart from an ordinary deleted subdirectory of a repo, which carries
# no such marker. The marker's position also locates the main repo: everything
# before it is the repo candidate. Used only by the worktree backfill, never by
# live detection (which has the real ``.git`` to read).

# A worktree container segment: ``worktree``/``worktrees`` with an optional
# leading ``.``/``-``/``_`` (e.g. ``.worktrees``).
_WORKTREE_CONTAINER_RE = re.compile(r'^[-._]?worktrees?$', re.IGNORECASE)
# A directly-named worktree folder: the same stem followed by a ``-``/``.``/``_``
# separator and at least one more character (e.g. ``worktree-feature-x``).
_WORKTREE_NAMED_RE = re.compile(r'^[-._]?worktrees?[-._].+$', re.IGNORECASE)


def _is_worktree_marker_segment(segment: str, *, is_last: bool) -> bool:
    """True if *segment* marks a worktree. A directly-named ``worktree-*`` folder
    counts anywhere; a bare container (``.worktrees``) only counts when it is not
    the last segment — the worktree itself must live inside it."""
    return bool(
        _WORKTREE_NAMED_RE.match(segment)
        or (not is_last and _WORKTREE_CONTAINER_RE.match(segment))
    )


def _nearest_repo_root(start_dir: str) -> str | None:
    """Walk up from *start_dir* (inclusive) to the nearest directory whose
    ``.git`` is a directory (a real repository root); return its realpath, or
    None if none is found before the filesystem root. Lets a container nested in
    a subfolder (``<repo>/.claude/worktrees/<wt>``) still resolve to ``<repo>``."""
    current = start_dir
    while True:
        try:
            if os.path.isdir(os.path.join(current, '.git')):
                return os.path.realpath(current)
        except OSError:
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def resolve_worktree_repo_from_marker(directory: str) -> str | None:
    """Resolve the main-repo root of a (possibly deleted) worktree from a
    worktree marker in its *directory* path. Returns the repo root realpath, or
    None when the path carries no marker or no repository exists above it.

    Splits at the leftmost marker segment — everything before it is the repo
    candidate — then walks up from there to the nearest existing ``.git``
    directory on disk. Filesystem-only; *directory* itself need not exist.
    """
    segments = os.path.normpath(directory).split(os.sep)
    last = len(segments) - 1
    for i in range(1, len(segments)):
        seg = segments[i]
        if not seg:
            continue
        if _is_worktree_marker_segment(seg, is_last=(i == last)):
            prefix = os.sep.join(segments[:i]) or os.sep
            return _nearest_repo_root(prefix)
    return None


def path_has_worktree_marker(directory: str) -> bool:
    """True if any segment of *directory* looks like a worktree marker (a
    container like ``.worktrees`` or a directly-named ``worktree-*`` folder),
    regardless of position. Looser than :func:`resolve_worktree_repo_from_marker`
    (no position constraint, no repo walk-up): used only to flag, in the
    backfill logs, a path that *looks* like a worktree but could not be linked."""
    for seg in os.path.normpath(directory).split(os.sep):
        if seg and (_WORKTREE_NAMED_RE.match(seg) or _WORKTREE_CONTAINER_RE.match(seg)):
            return True
    return False


class GitError(Exception):
    """Raised when a git operation fails."""

    def __init__(self, message: str):
        super().__init__(message)
        self.ui_message = getattr(message, "ui_message", None)
