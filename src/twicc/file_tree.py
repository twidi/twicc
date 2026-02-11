"""File tree business logic: directory listing and file search.

Extracted from views.py to keep views as thin HTTP wrappers.
"""

import os
import subprocess
from collections import deque

from django.http import Http404, JsonResponse

from twicc.core.models import Project, Session

NODE_THRESHOLD = 200  # Max cumulative nodes before stopping expansion


def validate_path(project_id, dir_path, session_id=None):
    """Validate that a directory path is allowed for a given project and optional session.

    When session_id is provided, also checks session-specific directories (cwd, git_directory).
    When session_id is None (project-level / draft sessions), only checks project.directory.

    Checks that:
    1. The project exists (and the session exists and is associated, if provided).
    2. The requested path is within one of the allowed base directories:
       - project.directory (always)
       - session.cwd (if session provided and set)
       - session.git_directory (if session provided and set)

    Returns (session, dir_path, error_response) where error_response is None on success.
    Session is None when no session_id is provided.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    session = None
    if session_id:
        try:
            session = Session.objects.get(id=session_id, project_id=project_id)
        except Session.DoesNotExist:
            raise Http404("Session not found")

    if not dir_path:
        return session, None, JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    # Build list of allowed base directories
    allowed_bases = []
    if project.git_root:
        allowed_bases.append(os.path.normpath(project.git_root))
    elif project.directory:
        allowed_bases.append(os.path.normpath(project.directory))
    if session:
        if session.cwd:
            allowed_bases.append(os.path.normpath(session.cwd))
        if session.git_directory:
            allowed_bases.append(os.path.normpath(session.git_directory))

    # Check that the requested path is within at least one allowed base
    path_allowed = False
    for base in allowed_bases:
        try:
            common = os.path.commonpath([base, dir_path])
            if common == base:
                path_allowed = True
                break
        except ValueError:
            continue

    if not path_allowed:
        return session, dir_path, JsonResponse(
            {"error": "Path is outside the allowed directories"}, status=403
        )

    if not os.path.isdir(dir_path):
        return session, dir_path, JsonResponse({"error": "Directory not found"}, status=404)

    return session, dir_path, None


def get_directory_tree(dir_path, show_hidden=False, show_ignored=False):
    """Build a directory tree using BFS with a node budget.

    Returns a dict with the tree structure:
        {
            "name": "root-dir-name",
            "type": "directory",
            "loaded": true,
            "is_git": true,
            "children": [
                {"name": "file.py", "type": "file"},
                {"name": "subdir", "type": "directory", "loaded": false},
            ]
        }

    Directories with "loaded": true have their full children list.
    Directories with "loaded": false have no children — the frontend should
    fetch them on demand by calling the endpoint with their path.
    The root-level "is_git" flag indicates if the directory is in a git repo.
    """
    # ── Git-ignore filtering ─────────────────────────────────────────────────
    # Detect whether we're in a git repo. If so, we'll use "git check-ignore"
    # to filter out gitignored entries from each directory listing.

    git_root = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=dir_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_root = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    def filter_gitignored(abs_paths):
        """Filter out gitignored paths using git check-ignore.

        Takes a list of absolute paths, returns the set of paths that are
        NOT ignored. Directories should have a trailing slash for correct
        matching of directory-only gitignore patterns.
        """
        if not git_root or not abs_paths:
            return set(abs_paths)
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--stdin"],
                cwd=git_root,
                input="\n".join(abs_paths),
                capture_output=True,
                text=True,
                timeout=5,
            )
            ignored = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
            return set(abs_paths) - ignored
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return set(abs_paths)

    def list_directory(abs_dir_path):
        """List one directory level, returning sorted (name, type) pairs.

        Respects show_hidden and show_ignored options from the outer scope.
        Directories are sorted first (case-insensitive), then files.
        """
        entries = []
        try:
            with os.scandir(abs_dir_path) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    entries.append(entry)
        except PermissionError:
            return []

        if not entries:
            return []

        # Filter gitignored entries in a single batch call
        if git_root and not show_ignored:
            # Build absolute paths for check-ignore; add trailing / for dirs
            path_map = {}
            for entry in entries:
                check_path = entry.path + "/" if entry.is_dir(follow_symlinks=False) else entry.path
                path_map[check_path] = entry
            not_ignored = filter_gitignored(list(path_map.keys()))
            entries = [path_map[p] for p in not_ignored]

        # Sort: directories first, then files, both alphabetically case-insensitive
        result = []
        for entry in entries:
            is_dir = entry.is_dir(follow_symlinks=False)
            result.append((entry.name, "directory" if is_dir else "file"))
        result.sort(key=lambda x: (0 if x[1] == "directory" else 1, x[0].lower()))
        return result

    # ── BFS tree construction with node budget ───────────────────────────────
    # Start with the root directory. Read its children (one level). For each
    # child directory, queue it for expansion. Process the queue in BFS order.
    # Once the node count reaches the threshold, stop expanding — all remaining
    # queued directories stay as "loaded": false.

    tree = {
        "name": os.path.basename(dir_path),
        "type": "directory",
        "loaded": True,
        "is_git": git_root is not None,
        "children": [],
    }
    node_count = 0

    # Queue entries: (absolute_path, tree_node_to_populate)
    expand_queue = deque()
    expand_queue.append((dir_path, tree))

    while expand_queue:
        current_abs_path, current_node = expand_queue.popleft()

        # If the budget was exhausted after this node was queued, convert it
        # to a stub — don't waste I/O reading its children.
        if node_count > NODE_THRESHOLD:
            current_node["loaded"] = False
            del current_node["children"]
            continue

        children_entries = list_directory(current_abs_path)

        for name, entry_type in children_entries:
            node_count += 1

            if entry_type == "directory":
                child_abs_path = os.path.join(current_abs_path, name)
                if node_count <= NODE_THRESHOLD:
                    # Budget available: create as loaded, queue for expansion
                    child_node = {"name": name, "type": "directory", "loaded": True, "children": []}
                    current_node["children"].append(child_node)
                    expand_queue.append((child_abs_path, child_node))
                else:
                    # Budget exhausted: stub, no expansion
                    current_node["children"].append({"name": name, "type": "directory", "loaded": False})
            else:
                current_node["children"].append({"name": name, "type": "file"})

    return tree


def search_files(dir_path, query, max_results=50, show_hidden=False, show_ignored=False):
    """Search for files matching a query.

    Two search modes:
    - Subsequence (default): each character of the query must appear in order
      in the file path. E.g. "vw" matches "views.py".
    - Exact substring: if the query starts with a quote (' or "), the rest is
      matched as a contiguous substring. A trailing matching quote is stripped.
      E.g. "foo => *foo*, "foo" => *foo*, 'bar => *bar*.

    Returns a dict with the tree structure containing only branches leading
    to matching files, plus total/truncated metadata:
        {
            "name": "<root dir name>",
            "type": "directory",
            "loaded": true,
            "children": [ ... ],
            "total": 5,
            "truncated": false
        }

    Results are sorted by best match (filename match > contiguous > shorter path),
    then the top N are assembled into a tree.
    """
    root_name = os.path.basename(dir_path)

    if not query:
        return {
            "name": root_name, "type": "directory", "loaded": True,
            "children": [], "total": 0, "truncated": False,
        }

    # Get all file paths
    git_cmd = ["git", "ls-files", "--cached", "--others"]
    if not show_ignored:
        git_cmd.append("--exclude-standard")
    try:
        result = subprocess.run(
            git_cmd,
            cwd=dir_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            file_paths = [line for line in result.stdout.strip().split("\n") if line]
        else:
            file_paths = None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        file_paths = None

    if file_paths is None:
        # Fallback: walk filesystem (limit to 50k files)
        file_paths = []
        max_files = 50000
        for root, dirs, files in os.walk(dir_path):
            if not show_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_root = os.path.relpath(root, dir_path)
            for f in files:
                if not show_hidden and f.startswith("."):
                    continue
                if rel_root == ".":
                    file_paths.append(f)
                else:
                    file_paths.append(os.path.join(rel_root, f))
                if len(file_paths) >= max_files:
                    break
            if len(file_paths) >= max_files:
                break

    # Filter hidden files from git output if not showing hidden
    if not show_hidden and file_paths:
        file_paths = [
            fp for fp in file_paths
            if not any(part.startswith(".") for part in fp.split(os.sep))
        ]

    # Determine search mode: exact substring vs subsequence.
    # If query starts with a quote (' or "), use exact substring matching.
    # A trailing matching quote is stripped but not searched for.
    # Examples: foo => *f*o*o*  |  "foo => *foo*  |  "foo" => *foo*
    exact_mode = False
    if query and query[0] in ('"', "'"):
        exact_mode = True
        quote_char = query[0]
        query = query[1:]  # strip leading quote
        if query and query[-1] == quote_char:
            query = query[:-1]  # strip trailing matching quote
        if not query:
            return {
                "name": root_name, "type": "directory", "loaded": True,
                "children": [], "total": 0, "truncated": False,
            }

    lower_query = query.lower()
    scored_results = []

    for fp in file_paths:
        lower_fp = fp.lower()

        if exact_mode:
            # Exact substring match
            if lower_query not in lower_fp:
                continue
        else:
            # Subsequence match
            qi = 0
            for ch in lower_fp:
                if qi < len(lower_query) and ch == lower_query[qi]:
                    qi += 1
            if qi != len(lower_query):
                continue

        # Score: prefer filename matches over path matches, shorter paths, consecutive chars
        filename = os.path.basename(fp).lower()

        if exact_mode:
            filename_match = lower_query in filename
        else:
            # Bonus for matching in filename portion
            qi_fn = 0
            for ch in filename:
                if qi_fn < len(lower_query) and ch == lower_query[qi_fn]:
                    qi_fn += 1
            filename_match = qi_fn == len(lower_query)

        # Bonus for consecutive character matches (contiguous substring)
        contiguous_bonus = 1 if lower_query in lower_fp else 0

        # Score: filename match > contiguous > shorter path
        score = (
            (1 if filename_match else 0),  # filename match first
            contiguous_bonus,               # contiguous match second
            -len(fp),                       # shorter paths preferred
        )
        scored_results.append((score, fp))

    # Sort by score (best first) then take top N
    scored_results.sort(key=lambda x: x[0], reverse=True)
    total = len(scored_results)
    truncated = total > max_results
    result_paths = [fp for _, fp in scored_results[:max_results]]

    # Build a tree from the matching paths (same format as directory-tree)
    tree = {"name": root_name, "type": "directory", "loaded": True, "children": []}
    dir_nodes = {}  # "path/segments" -> node dict

    for fp in result_paths:
        parts = fp.replace("\\", "/").split("/")

        # Create all parent directories
        for i in range(len(parts) - 1):
            dir_key = "/".join(parts[: i + 1])
            if dir_key not in dir_nodes:
                parent_key = "/".join(parts[:i]) if i > 0 else None
                parent_node = dir_nodes[parent_key] if parent_key else tree
                new_dir = {"name": parts[i], "type": "directory", "loaded": True, "children": []}
                parent_node["children"].append(new_dir)
                dir_nodes[dir_key] = new_dir

        # Add the file itself
        parent_key = "/".join(parts[:-1]) if len(parts) > 1 else None
        parent_node = dir_nodes[parent_key] if parent_key else tree
        parent_node["children"].append({"name": parts[-1], "type": "file"})

    # Sort children: directories first (alphabetically), then files (alphabetically)
    def sort_children(node):
        if "children" in node:
            node["children"].sort(key=lambda c: (0 if c["type"] == "directory" else 1, c["name"].lower()))
            for child in node["children"]:
                sort_children(child)

    sort_children(tree)

    tree["total"] = total
    tree["truncated"] = truncated

    return tree
