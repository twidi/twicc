"""Project icons — discovery, storage, resolution.

A project may show an icon image instead of its generated color dot. The icon
belongs to a git REPOSITORY (keyed by git root), shared by every project under
that root; any single project may override, and a project whose own dir is
above the git root ("umbrella") or is a worktree inherits via an explicit
anchor. Icons are copied+normalized into ``<data_dir>/project-icons/`` so they
survive deletion of the source file.

Storage layout::

    <data_dir>/project-icons/
      repo-<sha256(git_root)[:16]>/     # a repository's shared icon
        manifest.json                   # {scanned_at, icon_token|null, origin, source_path}
        icon-<sha8_of_content>.png      # normalized bytes (content-hashed name)
      proj-<sha256(project_id)[:16]>/   # a per-project override
        icon-<sha8_of_content>.png

Repo-icon state is the source of truth in each ``manifest.json`` and is mirrored
into the in-memory cache below (like ``twicc.projects._project_git_roots``), so
the query-free serializer resolves an icon URL with zero disk I/O.

Design: docs/archives/plans/2026-07-17-project-icons-design.md.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
from datetime import datetime, UTC
from pathlib import Path

import orjson
from asgiref.sync import sync_to_async

from twicc.paths import get_project_icons_dir

logger = logging.getLogger(__name__)

# --- Project.icon state sentinels -----------------------------------------
ICON_INHERIT = "inherit"
ICON_NONE = "none"

# --- Auto-discovery config -------------------------------------------------
# We make NO assumption about where a favicon/logo lives — every project is
# organized differently (repo root, public/, frontend/public/, a Django app's
# static/ tree, ...). Instead we search the repo recursively, breadth-first,
# for a whitelist of icon file NAMES, and the SHALLOWEST match wins. Bounded by
# a max depth and a visited-directory budget; heavy dirs and nested git repos
# are skipped so the cost stays small (a repo with a shallow icon stops early;
# only icon-less repos walk deep, capped by the budget).
DISCOVERY_MAX_DEPTH = 8
DISCOVERY_MAX_DIRS = 2000
# Manual "scan" (the edit dialog's picker): collect up to this many candidate
# paths, return up to this many normalized previews (deduped by content).
SCAN_MAX_CANDIDATES = 40
SCAN_MAX_PREVIEWS = 16

# Icon file names (case-insensitive): apple-touch-icon / favicon / icon / logo,
# with optional -<variant> suffixes (e.g. favicon-32x32, logo-dark), in a
# servable raster/vector format.
_ICON_NAME_RE = re.compile(
    r"^(apple-touch-icon|favicon|icon|logo)(?:[-_][0-9a-z]+)*\.(svg|png|ico|jpe?g|webp|gif)$",
    re.IGNORECASE,
)
# Within one depth level: role dominates, then format, then larger declared
# size. A square-optimized favicon/app-icon beats a (possibly wide) brand logo;
# svg beats png beats ico.
_ROLE_RANK = {"apple-touch-icon": 0, "favicon": 1, "icon": 2, "logo": 3}
_FORMAT_RANK = {"svg": 0, "png": 1, "ico": 2, "webp": 3, "jpg": 4, "jpeg": 4, "gif": 5}


def _icon_score(name: str) -> tuple[int, int, int] | None:
    """Rank of an icon filename within its depth level (lower = better), or
    ``None`` when *name* is not a recognized icon. Key: (role, format,
    -declared_size)."""
    m = _ICON_NAME_RE.match(name)
    if not m:
        return None
    role = m.group(1).lower()
    ext = m.group(2).lower()
    size_match = re.search(r"[-_](\d+)x\d+", name.lower())
    size = int(size_match.group(1)) if size_match else 0
    return (_ROLE_RANK[role], _FORMAT_RANK.get(ext, 9), -size)

# --- Normalization ---------------------------------------------------------
ICON_MAX_DIM = 256  # px; the badge is tiny, so a small square-ish source suffices
_SVG_EXT = ".svg"

# --- Bounded downward scan (umbrella projects) -----------------------------
SCAN_MAX_DEPTH = 2
SCAN_MAX_DIRS = 200
# Directories skipped by BOTH the icon search and the umbrella git scan. Hidden
# dirs (name starting with ".") are skipped too, so most build/tool caches are
# covered even when not listed here.
SCAN_SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "env", "dist", "build", "__pycache__",
    ".git", ".tox", ".mypy_cache", ".pytest_cache", "target", "vendor",
    ".next", ".nuxt", ".cache", "coverage", "site-packages", "out",
    "bower_components", "Pods",
})


# The AUTO-discovered icon of a git repo, keyed by BUCKET name (``repo-<hash>``):
#   absent    -> not scanned this run (ensure_project_icon will scan)
#   token=str -> repo has an auto icon (that stored filename)
#   None      -> scanned this run, nothing found (transient, not persisted; a
#                favicon added later is picked up on the next run)
# This is purely the auto layer — user choices live per-project on Project.icon
# and cascade down the project chain (resolved client-side). Loaded from
# manifests at startup by load_repo_icon_cache, updated live on discovery.
_repo_icon_states: dict[str, str | None] = {}


# ===========================================================================
# Bucket + manifest helpers
# ===========================================================================

def _repo_bucket(git_root: str) -> str:
    """Opaque bucket dir name for a repository's shared icon. Keyed by the
    ``git_root`` string as stored on the row (discovery and serialization use
    the same value), so no realpath I/O is needed to look it up."""
    return f"repo-{hashlib.sha256(git_root.encode('utf-8')).hexdigest()[:16]}"


def _proj_bucket(project_id: str) -> str:
    """Opaque bucket dir name for a per-project override."""
    return f"proj-{hashlib.sha256(project_id.encode('utf-8')).hexdigest()[:16]}"


def _manifest_path(bucket_dir: Path) -> Path:
    return bucket_dir / "manifest.json"


def _read_manifest(bucket_dir: Path) -> dict | None:
    try:
        return orjson.loads(_manifest_path(bucket_dir).read_bytes())
    except (FileNotFoundError, NotADirectoryError, orjson.JSONDecodeError, OSError):
        return None


def _write_manifest(bucket_dir: Path, *, token: str | None, source_path: str | None) -> None:
    bucket_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "icon_token": token,
        "source_path": source_path,
    }
    _manifest_path(bucket_dir).write_bytes(orjson.dumps(payload))


def _remove_bucket(bucket_dir: Path) -> None:
    try:
        shutil.rmtree(bucket_dir)
    except (FileNotFoundError, OSError):
        pass


def _icon_filename(out_bytes: bytes, ext: str) -> str:
    """Content-hashed filename so changed bytes yield a new URL (cache-busting)."""
    return f"icon-{hashlib.sha256(out_bytes).hexdigest()[:8]}{ext}"


def _store_icon(bucket_dir: Path, out_bytes: bytes, ext: str) -> str:
    """Write ``out_bytes`` as ``icon-<sha8><ext>`` in ``bucket_dir`` and drop
    any previous icon file (old version). Returns the filename."""
    bucket_dir.mkdir(parents=True, exist_ok=True)
    fname = _icon_filename(out_bytes, ext)
    (bucket_dir / fname).write_bytes(out_bytes)
    for f in bucket_dir.glob("icon-*"):
        if f.name != fname and f.is_file():
            try:
                f.unlink()
            except OSError:
                pass
    return fname


# ===========================================================================
# Normalization (Pillow) — pure, no Django
# ===========================================================================

def _looks_like_svg(data: bytes) -> bool:
    """True if ``data`` parses as XML whose root element is ``<svg>``."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        return False
    tag = root.tag
    return isinstance(tag, str) and tag.rsplit("}", 1)[-1].lower() == "svg"


def normalize_icon_bytes(data: bytes, ext: str) -> tuple[bytes, str] | None:
    """Normalize an icon image for storage/serving, or return ``None`` on
    failure. SVG is validated (well-formed ``<svg>``) and kept as-is; every
    raster input (``.ico``/``.png``/``.jpg``/``.webp``/``.gif``) is decoded and
    re-encoded as a size-bounded PNG (``.ico`` uses its largest embedded frame,
    which Pillow selects by default). Rendered via ``<img>``, so SVG is safe."""
    if ext.lower() == _SVG_EXT:
        return (data, _SVG_EXT) if _looks_like_svg(data) else None

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as e:  # pragma: no cover — Pillow is a declared dependency
        logger.warning("Pillow unavailable, cannot normalize project icon: %s", e)
        return None

    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # force-decode to surface errors here
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    w, h = img.size
    if not w or not h:
        return None
    if max(w, h) > ICON_MAX_DIM:
        scale = ICON_MAX_DIM / max(w, h)
        try:
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        except Exception:
            return None

    out = io.BytesIO()
    try:
        img.save(out, format="PNG", optimize=True)
    except Exception:
        try:
            img.convert("RGBA").save(out, format="PNG", optimize=True)
        except Exception:
            return None
    return out.getvalue(), ".png"


# ===========================================================================
# Discovery — pure, no Django
# ===========================================================================

def _discover_source(git_root: str) -> str | None:
    """Return the path of the best favicon/logo anywhere under ``git_root``, or
    ``None``. Breadth-first by depth: the **shallowest** matching file wins
    (ties broken by :func:`_icon_score`). No location assumptions. Bounded by
    :data:`DISCOVERY_MAX_DEPTH` and :data:`DISCOVERY_MAX_DIRS`; skips
    :data:`SCAN_SKIP_DIRS`, hidden dirs, and nested git repositories (a worktree
    or vendored repo belongs to another project). A repo whose icon is shallow
    stops early; only an icon-less repo walks deep, capped by the dir budget."""
    best_path: str | None = None
    best_score: tuple[int, int, int] | None = None
    visited = 0
    current = [git_root]
    depth = 0
    while current and depth <= DISCOVERY_MAX_DEPTH:
        next_level: list[str] = []
        for directory in current:
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            visited += 1
            for entry in entries:
                try:
                    if entry.is_file():
                        score = _icon_score(entry.name)
                        if score is not None and (best_score is None or score < best_score):
                            best_score, best_path = score, entry.path
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name in SCAN_SKIP_DIRS or entry.name.startswith("."):
                            continue
                        if os.path.exists(os.path.join(entry.path, ".git")):
                            continue  # nested git repo → another project
                        next_level.append(entry.path)
                except OSError:
                    continue
            if visited >= DISCOVERY_MAX_DIRS:
                return best_path  # budget exhausted
        if best_path is not None:
            return best_path  # shallowest depth with a match — fully scanned
        current = next_level
        depth += 1
    return best_path


def _collect_icon_candidates(git_root: str) -> list[tuple[int, tuple[int, int, int], str]]:
    """Every icon file under *git_root* as ``(depth, score, path)``, ranked
    best-first (shallowest, then :func:`_icon_score`). Unlike
    :func:`_discover_source` it does NOT stop at the first depth — it gathers
    across depths for the user to choose. Same bounds/exclusions."""
    out: list[tuple[int, tuple[int, int, int], str]] = []
    visited = 0
    current = [git_root]
    depth = 0
    while current and depth <= DISCOVERY_MAX_DEPTH:
        next_level: list[str] = []
        for directory in current:
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            visited += 1
            for entry in entries:
                try:
                    if entry.is_file():
                        score = _icon_score(entry.name)
                        if score is not None:
                            out.append((depth, score, entry.path))
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name in SCAN_SKIP_DIRS or entry.name.startswith("."):
                            continue
                        if os.path.exists(os.path.join(entry.path, ".git")):
                            continue
                        next_level.append(entry.path)
                except OSError:
                    continue
            if visited >= DISCOVERY_MAX_DIRS or len(out) >= SCAN_MAX_CANDIDATES:
                out.sort(key=lambda t: (t[0], t[1]))
                return out[:SCAN_MAX_CANDIDATES]
        current = next_level
        depth += 1
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def scan_repo_icons(git_root: str) -> list[dict]:
    """Normalized previews of the icon candidates under *git_root*, for the edit
    dialog's picker. Each entry: ``{name, rel_path, depth, image}`` where
    ``image`` is a ``data:`` URI of the normalized bytes (exactly what would be
    stored). Deduped by content, ranked best-first, capped."""
    import base64

    result: list[dict] = []
    seen: set[str] = set()
    for depth, _score, path in _collect_icon_candidates(git_root):
        if len(result) >= SCAN_MAX_PREVIEWS:
            break
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        normalized = normalize_icon_bytes(data, os.path.splitext(path)[1])
        if normalized is None:
            continue
        out_bytes, out_ext = normalized
        digest = hashlib.sha256(out_bytes).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        mime = "image/svg+xml" if out_ext == _SVG_EXT else "image/png"
        result.append({
            "name": os.path.basename(path),
            "rel_path": os.path.relpath(path, git_root),
            "depth": depth,
            "image": f"data:{mime};base64,{base64.b64encode(out_bytes).decode('ascii')}",
        })
    return result


def find_single_git_below(directory: str) -> str | None:
    """Bounded downward scan for the ONE git repo under *directory* (the
    "umbrella" case). Returns its root, or ``None`` if there are zero, more than
    one (ambiguous), or the visit budget is exceeded. Icon-only — never used to
    set ``Project.git_root``."""
    base = Path(directory)
    found: list[str] = []
    visited = 0
    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack:
        current, depth = stack.pop()
        if current != base:
            try:
                if (current / ".git").exists():
                    found.append(str(current))
                    if len(found) > 1:
                        return None
                    continue  # a repo root — don't descend into it
            except OSError:
                pass
        if depth >= SCAN_MAX_DEPTH:
            continue
        try:
            children = list(os.scandir(current))
        except OSError:
            continue
        for entry in children:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name in SCAN_SKIP_DIRS or entry.name.startswith("."):
                continue
            visited += 1
            if visited > SCAN_MAX_DIRS:
                return None
            stack.append((Path(entry.path), depth + 1))
    return found[0] if len(found) == 1 else None


# ===========================================================================
# Cache load (startup)
# ===========================================================================

def load_repo_icon_cache() -> None:
    """Populate the in-memory repo-icon cache from persisted manifests. Called
    at startup, like ``twicc.projects.load_project_git_roots``. Only found icons
    are persisted; scanned-empty is never persisted, so it re-scans."""
    _repo_icon_states.clear()
    base = get_project_icons_dir()
    try:
        entries = list(base.iterdir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return
    for d in entries:
        if not d.name.startswith("repo-"):
            continue
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        man = _read_manifest(d)
        token = man.get("icon_token") if man else None
        if token:
            _repo_icon_states[d.name] = token


# ===========================================================================
# Resolution bricks (serializer path) — in-memory only, query-free
# ===========================================================================
#
# The serializer exposes two independent URLs per project; the EFFECTIVE icon is
# resolved CLIENT-SIDE by walking the project chain (utils/projectIcon.js), so
# that a manual override cascades to descendants like agent-defaults do:
#   - own override (this project's own icon) cascades down the chain,
#   - else the project's auto repo icon (the socle),
#   - else the color dot.

def project_own_icon_url(project) -> str | None:
    """This project's OWN manual override URL (or ``None``). Does NOT resolve
    inheritance — the client walks the chain."""
    v = project.icon or ICON_INHERIT
    if v in (ICON_INHERIT, ICON_NONE):
        return None
    return f"/project-icons/{_proj_bucket(project.id)}/{v}"


def project_repo_icon_url(project) -> str | None:
    """The AUTO-discovered repo icon URL for this project's anchor (or ``None``).
    The lowest-priority layer, shared by every project of the repo."""
    anchor = project.icon_anchor or project.git_root
    if not anchor:
        return None
    bucket = _repo_bucket(anchor)
    token = _repo_icon_states.get(bucket)
    return f"/project-icons/{bucket}/{token}" if token else None


# ===========================================================================
# Anchor resolution + discovery orchestration (touches the DB / filesystem)
# ===========================================================================

def resolve_icon_anchor_sync(project) -> str | None:
    """Git root this project inherits its repo icon from, or ``None``.

    - worktree -> its main repository's ``git_root`` (shares the main repo's
      icon, like it shares its color);
    - else the project's own ``git_root`` (upward walk);
    - else a bounded downward single-git scan (umbrella folder).
    """
    from twicc.core.models import Project

    if project.worktree_of_id:
        parent_root = (
            Project.objects.filter(id=project.worktree_of_id)
            .values_list("git_root", flat=True)
            .first()
        )
        if parent_root:
            return parent_root
    if project.git_root:
        return project.git_root
    if project.directory:
        return find_single_git_below(project.directory)
    return None


def _ensure_repo_icon_discovered(git_root: str) -> bool:
    """Ensure the auto repo icon for ``git_root`` is discovered+cached. Returns
    True only when an icon was NEWLY discovered this call (so the caller
    broadcasts). Sticky once found; scanned-empty re-scans each run."""
    bucket = _repo_bucket(git_root)
    if bucket in _repo_icon_states:
        return False  # found or scanned-empty this run
    bucket_dir = get_project_icons_dir() / bucket
    man = _read_manifest(bucket_dir)
    if man and man.get("icon_token"):
        _repo_icon_states[bucket] = man["icon_token"]
        return False
    # Scan.
    source = _discover_source(git_root)
    if source is None:
        _repo_icon_states[bucket] = None  # scanned-empty (transient, not persisted)
        return False
    try:
        data = Path(source).read_bytes()
    except OSError:
        _repo_icon_states[bucket] = None
        return False
    normalized = normalize_icon_bytes(data, os.path.splitext(source)[1])
    if normalized is None:
        _repo_icon_states[bucket] = None
        return False
    out_bytes, out_ext = normalized
    token = _store_icon(bucket_dir, out_bytes, out_ext)
    _write_manifest(bucket_dir, token=token, source_path=source)
    _repo_icon_states[bucket] = token
    return True


def _persist_icon_anchor_sync(project_id: str, anchor: str | None) -> None:
    from twicc.core.models import Project

    Project.objects.filter(id=project_id).update(icon_anchor=anchor)


async def _ensure_project_icon(project) -> tuple[str | None, bool]:
    """Resolve+persist the project's icon anchor and ensure its repo icon is
    discovered. Returns ``(anchor, newly_discovered)``. Never writes
    ``Project.icon``. Best-effort — filesystem/decode errors yield no icon.
    Only the anchor write takes the DB write lock, never the filesystem scans."""
    from twicc.providers.db_writer import run_under_db_write_lock

    anchor = await sync_to_async(resolve_icon_anchor_sync)(project)
    # Persist icon_anchor only when it differs from the own git_root.
    desired = anchor if (anchor and anchor != project.git_root) else None
    if project.icon_anchor != desired:
        await run_under_db_write_lock(lambda: sync_to_async(_persist_icon_anchor_sync)(project.id, desired))
        project.icon_anchor = desired
    if not anchor:
        return None, False
    return anchor, await sync_to_async(_ensure_repo_icon_discovered)(anchor)


async def _broadcast_repo_icon_change(git_root: str) -> None:
    """Re-broadcast ``project_updated`` for every project anchored on
    ``git_root`` — their ``repo_icon_url`` changed, so the client re-resolves.
    (Descendants inheriting it re-resolve reactively from these updates.)"""
    from django.db.models import Q

    from twicc.core.models import Project
    from twicc.projects import _broadcast_project_updated

    ids = await sync_to_async(lambda: list(
        Project.objects.filter(
            Q(git_root=git_root) | Q(icon_anchor=git_root)
        ).values_list("id", flat=True).distinct()
    ))()
    for pid in ids:
        await _broadcast_project_updated(pid)


async def ensure_project_icon(project) -> None:
    """Async wrapper for the registration path: ensure anchor + repo icon,
    broadcasting when a repo icon is newly discovered."""
    anchor, changed = await _ensure_project_icon(project)
    if changed and anchor:
        await _broadcast_repo_icon_change(anchor)


async def discover_all_project_icons() -> int:
    """One-shot startup sweep over every project (the "initial sync" of icons).
    Cheap after the first run (found icons short-circuit via manifest; empty
    repos re-scan a handful of paths). Returns the count of repo icons newly
    discovered. Best-effort per project."""
    from twicc.core.models import Project

    await sync_to_async(load_repo_icon_cache)()
    projects = await sync_to_async(lambda: list(
        Project.objects.exclude(directory__isnull=True)
    ))()
    discovered = 0
    for project in projects:
        try:
            anchor, changed = await _ensure_project_icon(project)
        except Exception:
            logger.exception("Project icon discovery failed for %s", project.id)
            continue
        if changed and anchor:
            await _broadcast_repo_icon_change(anchor)
            discovered += 1
    return discovered


# ===========================================================================
# Explicit user mutations (frontend API)
# ===========================================================================

def set_project_icon_override_sync(project_id: str, data: bytes, ext: str) -> str | None:
    """Store an uploaded image as *project_id*'s per-project override and set
    ``Project.icon`` to its token. Returns the token, or ``None`` if the image
    is unusable."""
    from twicc.core.models import Project

    normalized = normalize_icon_bytes(data, ext)
    if normalized is None:
        return None
    out_bytes, out_ext = normalized
    token = _store_icon(get_project_icons_dir() / _proj_bucket(project_id), out_bytes, out_ext)
    Project.objects.filter(id=project_id).update(icon=token)
    return token


def set_project_icon_state_sync(project_id: str, state: str) -> None:
    """Set ``Project.icon`` to ``"inherit"`` or ``"none"`` and drop any stored
    override bytes."""
    from twicc.core.models import Project

    Project.objects.filter(id=project_id).update(icon=state)
    _remove_bucket(get_project_icons_dir() / _proj_bucket(project_id))
