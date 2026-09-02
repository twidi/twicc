"""
Plan-document detection shared by every provider's compute pipeline.

Feeds ``Session.plan_paths``: the list of plan-like documents (plans, specs,
handoffs, design notes...) a session touched, detected from file-edit tool
calls and from shell commands that write files. Pure module — no ORM, no
Django imports — so it is importable from the background-compute subprocess,
the live watcher and tests alike.

Path convention (mirrors the ``Session.plan_paths`` model comment): entries
store a POSIX path relative to the session's project directory when the file
lives under it (portable across worktree removal — the frontend falls back to
the ``worktree_of`` parent project), and an absolute path otherwise (e.g. the
native Claude plan under ``<claude home>/plans/``).
"""

from __future__ import annotations

import os
import re
import shlex
from datetime import datetime
from typing import NamedTuple

# --- Pattern configuration -------------------------------------------------
# Only document-like files are ever considered (never code files).

DOC_EXTENSIONS = {'.md', '.markdown', '.mdx', '.txt', '.html', '.htm', '.rst', '.adoc', '.mmd'}

# Matched against filename-stem tokens (split on -_. and spaces, lowercased):
# token matching, not substring, so "airplane.md" does not match "plan".
NAME_KEYWORDS = {
    'plan', 'plans', 'planning',
    'spec', 'specs', 'specification',
    'design', 'architecture', 'adr', 'rfc', 'prd',
    'proposal', 'proposals',
    'handoff', 'handover',
    'note', 'notes',
    'research', 'analysis', 'findings', 'investigation', 'audit',
    'review', 'postmortem', 'retro', 'retrospective',
    'roadmap', 'strategy', 'brainstorm', 'brainstorming',
    'decision', 'decisions', 'requirements', 'brief',
    'report', 'summary', 'outline', 'draft',
    'todo', 'todos', 'checklist',
    'runbook', 'playbook', 'migration',
    'worklog', 'devlog', 'status',
    'ideas', 'questions',
}

# A file with a DOC_EXTENSION under any ancestor directory with one of these
# names matches even if its own name doesn't (e.g. docs/plans/refactor.md).
DIR_KEYWORDS = {
    'plans', 'specs', 'design', 'designs', 'adr', 'adrs', 'rfcs',
    'handoffs', 'proposals', 'research', 'notes', 'decisions',
    'postmortems', 'planning', 'brainstorms',
}

# Filenames never tracked (compared on the lowercased stem, extension already
# stripped — so "CLAUDE.local.md" is excluded via the "claude.local" stem).
EXCLUDED_NAMES = {
    'readme', 'changelog', 'license', 'licence', 'contributing',
    'code_of_conduct', 'security', 'claude', 'claude.local', 'agents',
    'gemini', 'memory', 'skill',
}

# Path segments that disqualify (vendored/generated trees).
EXCLUDED_SEGMENTS = {
    'node_modules', '.git', 'vendor', 'vendored', 'dist', 'build',
    '__pycache__', '.venv', 'venv', 'site-packages',
}

_STEM_TOKEN_RE = re.compile(r'[-_.\s]+')


def is_plan_doc_path(path: str) -> bool:
    """True when ``path`` looks like a plan-like document worth tracking."""
    if not path or not isinstance(path, str):
        return False
    normalized = path.replace('\\', '/')
    segments = [s for s in normalized.split('/') if s]
    if not segments:
        return False
    if any(seg.lower() in EXCLUDED_SEGMENTS for seg in segments):
        return False
    filename = segments[-1]
    stem, ext = os.path.splitext(filename)
    if ext.lower() not in DOC_EXTENSIONS:
        return False
    stem_lower = stem.lower()
    if stem_lower in EXCLUDED_NAMES:
        return False
    tokens = [t for t in _STEM_TOKEN_RE.split(stem_lower) if t]
    if any(t in NAME_KEYWORDS for t in tokens):
        return True
    return any(seg.lower() in DIR_KEYWORDS for seg in segments[:-1])


# --- Events ------------------------------------------------------------------

class DocEditEvent(NamedTuple):
    """One detected write/delete of a plan-like document, from one JSONL line."""
    path: str            # absolute (providers cwd-join relative targets first)
    action: str          # 'write' | 'delete'
    source: str = 'detected'  # 'claude_plan' native plan-mode file, 'subagent' folded from a subagent


# Entry sources a main session's own writers cannot rebuild from its own JSONL
# lines (the native plan file is latched by the plans watcher / probed from
# disk, subagent docs are folded in by the subagents' own compute passes):
# preserved via :func:`fold_concurrent_entries` on every write.
FOLDED_SOURCES = frozenset({'claude_plan', 'subagent'})


# --- Shell heuristic ---------------------------------------------------------

# Redirection operators whose next token is a file being written.
_WRITE_REDIRECTS = {'>', '>>', '&>', '&>>', '>|'}
# Tokens that separate independent command segments.
_SEGMENT_SEPARATORS = {';', '|', '||', '&&', '&', '(', ')', ';;'}
# Redirect targets that are not real files.
_NON_FILE_TARGETS = {'/dev/null', '/dev/stdout', '/dev/stderr', '/dev/tty'}
# Leading wrapper commands to skip before identifying the real command.
_COMMAND_WRAPPERS = {'sudo', 'nohup', 'command', 'time'}
_SHELL_BINARIES = {'bash', 'sh', 'zsh', 'dash', 'ksh'}


def _tokenize_shell(command: str) -> list[str] | None:
    """Tokenize with operators split out; None when the command is unparseable."""
    lex = shlex.shlex(command, posix=True, punctuation_chars='();<>|&')
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None


def _is_fd_target(token: str) -> bool:
    return token.startswith('&') or token.isdigit() or token == '-'


def _analyze_segment(tokens: list[str]) -> list[tuple[str, str]]:
    """Extract (path, action) write/delete targets from one command segment."""
    targets: list[tuple[str, str]] = []
    args: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _WRITE_REDIRECTS or (tok.endswith('>') and tok.rstrip('>').isdigit()):
            # ">", ">>", "&>", "2>"... — next token is the written file.
            if i + 1 < len(tokens):
                target = tokens[i + 1]
                if target not in _NON_FILE_TARGETS and not _is_fd_target(target):
                    targets.append((target, 'write'))
                i += 2
                continue
        elif tok.startswith('<'):
            # Input redirection / heredoc marker: skip it and its operand.
            i += 2
            continue
        else:
            args.append(tok)
        i += 1

    # Skip wrappers (sudo, env VAR=x, ...) to find the actual command.
    while args and (args[0] in _COMMAND_WRAPPERS or '=' in args[0]):
        args.pop(0)
    if args and args[0] == 'env':
        args.pop(0)
        while args and ('=' in args[0] or args[0].startswith('-')):
            args.pop(0)
    if not args:
        return targets

    cmd = args[0].rsplit('/', 1)[-1]
    rest = args[1:]
    flags = [a for a in rest if a.startswith('-')]
    positional = [a for a in rest if not a.startswith('-')]

    if cmd == 'tee':
        targets.extend((p, 'write') for p in positional)
    elif cmd == 'sed':
        in_place = any(f == '-i' or f.startswith(('-i', '--in-place')) for f in flags)
        if in_place:
            # When the script comes via -e/-f/--expression/--file, every
            # remaining positional is an edited file; otherwise the first
            # positional is the inline script and the rest are the files.
            script_via_flag = False
            skip_next = False
            remaining: list[str] = []
            for a in rest:
                if skip_next:
                    skip_next = False
                    continue
                if a in ('-e', '--expression', '-f', '--file'):
                    script_via_flag = True
                    skip_next = True
                    continue
                if a.startswith(('--expression=', '--file=')):
                    script_via_flag = True
                    continue
                if a.startswith('-'):
                    continue
                remaining.append(a)
            files = remaining if script_via_flag else remaining[1:]
            targets.extend((p, 'write') for p in files)
    elif cmd in ('cp', 'mv'):
        if len(positional) >= 2:
            targets.append((positional[-1], 'write'))
    elif cmd == 'touch':
        targets.extend((p, 'write') for p in positional)
    elif cmd == 'rm':
        targets.extend((p, 'delete') for p in positional)

    return targets


def extract_shell_write_targets(command: str | list | tuple) -> list[tuple[str, str]]:
    """
    Conservative detection of files written/deleted by a shell command.

    Accepts a command string or an argv list (``["bash", "-lc", "script"]``
    unwraps to its script; other argv lists are analyzed as a single already
    tokenized segment). Returns ``[(path, action)]`` with paths as written
    (possibly relative — the caller cwd-joins and pattern-filters them).
    """
    if isinstance(command, (list, tuple)):
        tokens = [str(t) for t in command]
        if (
            len(tokens) >= 3
            and tokens[0].rsplit('/', 1)[-1] in _SHELL_BINARIES
            and tokens[1].startswith('-')
            and 'c' in tokens[1]
        ):
            return extract_shell_write_targets(tokens[2])
    elif isinstance(command, str):
        tokens = _tokenize_shell(command)
        if tokens is None and '<<' in command:
            # Heredoc bodies are prose — an unbalanced apostrophe in one makes
            # the whole command unparseable. The redirection we care about
            # (`cat > plan.md <<'EOF'`) sits before the heredoc marker: retry
            # on that prefix.
            tokens = _tokenize_shell(command.split('<<', 1)[0])
        if tokens is None:
            return []
    else:
        return []

    results: list[tuple[str, str]] = []
    segment: list[str] = []
    for tok in tokens:
        if tok in _SEGMENT_SEPARATORS:
            if segment:
                results.extend(_analyze_segment(segment))
                segment = []
        else:
            segment.append(tok)
    if segment:
        results.extend(_analyze_segment(segment))
    return results


# --- Entry list maintenance ---------------------------------------------------

def normalize_stored_path(path: str, project_root: str | None) -> str:
    """Absolute path -> stored form: POSIX-relative under the project root, absolute otherwise."""
    norm = os.path.normpath(path)
    if project_root:
        root = os.path.normpath(project_root)
        try:
            common = os.path.commonpath([norm, root])
        except ValueError:
            common = None
        if common == root and norm != root:
            return os.path.relpath(norm, root).replace(os.sep, '/')
    return norm


def _to_iso(timestamp: datetime | str | None) -> str | None:
    if isinstance(timestamp, datetime):
        return timestamp.isoformat()
    return timestamp or None


def _is_newer(candidate: str | None, current: str | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    # ISO-8601 strings from the same producer compare correctly as strings.
    return candidate > current


def apply_doc_edit_events(
    entries: list[dict],
    timed_events: list[tuple[DocEditEvent, datetime | str | None]],
    *,
    project_root: str | None,
) -> tuple[list[dict], bool]:
    """
    Fold ``timed_events`` (chronological) into a copy of ``entries``.

    Write events create entries (``created_at = updated_at = line timestamp``)
    or refresh ``updated_at``/``exists``; delete events flip ``exists`` to
    False (entries are never removed — history stays browsable). Returns
    ``(new_entries, changed)``.
    """
    result = [dict(e) for e in entries] if entries else []
    by_path = {e.get('path'): e for e in result}
    changed = False

    for event, timestamp in timed_events:
        stored = normalize_stored_path(event.path, project_root)
        ts = _to_iso(timestamp)
        entry = by_path.get(stored)
        if event.action == 'write':
            if entry is None:
                entry = {
                    'path': stored,
                    'exists': True,
                    'created_at': ts,
                    'updated_at': ts,
                    'source': event.source,
                }
                result.append(entry)
                by_path[stored] = entry
                changed = True
            else:
                if not entry.get('exists'):
                    entry['exists'] = True
                    changed = True
                if _is_newer(ts, entry.get('updated_at')):
                    entry['updated_at'] = ts
                    changed = True
        elif event.action == 'delete':
            if entry is not None:
                if entry.get('exists'):
                    entry['exists'] = False
                    changed = True
                if _is_newer(ts, entry.get('updated_at')):
                    entry['updated_at'] = ts
                    changed = True

    return result, changed


def resolve_stored_path(path: str, roots: list[str | None]) -> str | None:
    """Stored entry path -> absolute path.

    Absolute entries pass through; relative entries resolve against the first
    root where the file exists (worktree-portable: the ``worktree_of`` parent
    root catches docs whose worktree is gone), falling back to the first
    usable root when none matches (the caller surfaces the missing file).
    ``None`` for a relative path with no usable root. Mirrors the frontend's
    client-side resolution in ``PlanPane.vue``.
    """
    if os.path.isabs(path):
        return path
    usable_roots = [r for r in roots if r]
    if not usable_roots:
        return None
    for root in usable_roots:
        candidate = os.path.join(root, path)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(usable_roots[0], path)


def refresh_entries_existence(entries: list[dict], roots: list[str | None]) -> bool:
    """
    Re-probe ``exists`` for every entry (absolute as-is, relative against each
    root in order). Mutates entries in place; returns whether anything changed.
    """
    usable_roots = [r for r in roots if r]
    changed = False
    for entry in entries:
        path = entry.get('path')
        if not path:
            continue
        if os.path.isabs(path):
            exists = os.path.exists(path)
        elif not usable_roots:
            # No root to resolve against (the project lost its directory):
            # leave the stored flag alone rather than flipping it to False.
            continue
        else:
            exists = any(os.path.exists(os.path.join(root, path)) for root in usable_roots)
        if bool(entry.get('exists')) != exists:
            entry['exists'] = exists
            changed = True
    return changed


def fold_concurrent_entries(
    computed: list[dict], db_entries: list[dict] | None, *, sources: frozenset[str] | set[str],
) -> list[dict]:
    """
    Just-before-write guard for the concurrent ``plan_paths`` writers of a
    main session (its own JSONL sync/recompute vs the plans watcher vs its
    subagents' folds): fold into ``computed`` any entry from the freshly
    re-read DB row whose ``source`` is in ``sources`` and that is newer than
    (or absent from) the computed list — those sources are written by other
    writers and can't be rebuilt from the session's own lines. Returns a new
    list, ``computed`` untouched.
    """
    if not db_entries:
        return computed
    result = [dict(e) for e in computed]
    by_path = {e.get('path'): e for e in result}
    for db_entry in db_entries:
        if db_entry.get('source') not in sources:
            continue
        current = by_path.get(db_entry.get('path'))
        if current is None:
            result.append(dict(db_entry))
        elif _is_newer(db_entry.get('updated_at'), current.get('updated_at')):
            current.update(db_entry)
    return result
