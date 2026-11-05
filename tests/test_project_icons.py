"""Tests for project-icon discovery, normalization, storage and resolution."""

import asyncio
import io
from types import SimpleNamespace

import pytest

from twicc import project_icons as pi


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer is only started at app boot."""
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)


def _ensure(project):
    return asyncio.run(pi._ensure_project_icon(project))


def _png_bytes(size=(10, 10), color=(255, 0, 0, 255)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, format="PNG")
    return out.getvalue()


def _ico_bytes(size=(32, 32)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", size, (0, 128, 255, 255)).save(out, format="ICO")
    return out.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16"/></svg>'


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_png_kept_as_png():
    out = pi.normalize_icon_bytes(_png_bytes(), ".png")
    assert out is not None and out[1] == ".png"


def test_normalize_ico_converted_to_png():
    out = pi.normalize_icon_bytes(_ico_bytes(), ".ico")
    assert out is not None and out[1] == ".png"


def test_normalize_downscales_oversized():
    from PIL import Image

    out = pi.normalize_icon_bytes(_png_bytes(size=(1024, 512)), ".png")
    assert out is not None
    w, h = Image.open(io.BytesIO(out[0])).size
    assert max(w, h) == pi.ICON_MAX_DIM


def test_normalize_svg_kept_when_valid():
    out = pi.normalize_icon_bytes(SVG, ".svg")
    assert out == (SVG, ".svg")


def test_normalize_svg_rejects_non_svg():
    assert pi.normalize_icon_bytes(b"not xml at all", ".svg") is None
    assert pi.normalize_icon_bytes(b"<html></html>", ".svg") is None


def test_normalize_rejects_garbage_raster():
    assert pi.normalize_icon_bytes(b"\x00\x01\x02 not an image", ".png") is None


# ---------------------------------------------------------------------------
# Discovery priority
# ---------------------------------------------------------------------------

def test_discover_role_beats_format_at_same_depth(tmp_path):
    (tmp_path / "favicon.ico").write_bytes(b"x")
    (tmp_path / "logo.svg").write_bytes(b"x")
    (tmp_path / "apple-touch-icon.png").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) == str(tmp_path / "apple-touch-icon.png")


def test_discover_shallowest_wins_over_better_format(tmp_path):
    # A plain favicon.png at the root beats a favicon.svg nested two levels deep:
    # depth dominates (shallowest wins), format is only a same-depth tiebreak.
    (tmp_path / "favicon.png").write_bytes(b"x")
    (tmp_path / "frontend" / "public").mkdir(parents=True)
    (tmp_path / "frontend" / "public" / "favicon.svg").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) == str(tmp_path / "favicon.png")


def test_discover_finds_deeply_nested_icon(tmp_path):
    # No location assumption: an icon several levels down is still found.
    deep = tmp_path / "package" / "src" / "app" / "core" / "static" / "core"
    deep.mkdir(parents=True)
    (deep / "logo.png").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) == str(deep / "logo.png")


def test_discover_matches_sized_variant(tmp_path):
    (tmp_path / "favicon-32x32.png").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) == str(tmp_path / "favicon-32x32.png")


def test_discover_skips_heavy_dirs(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "favicon.ico").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) is None


def test_discover_skips_nested_git_repo(tmp_path):
    sub = tmp_path / "vendored"
    (sub / ".git").mkdir(parents=True)
    (sub / "favicon.ico").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) is None


def test_discover_respects_depth_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "DISCOVERY_MAX_DEPTH", 1)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "favicon.ico").write_bytes(b"x")
    assert pi._discover_source(str(tmp_path)) is None


def test_discover_none_when_absent(tmp_path):
    assert pi._discover_source(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# scan_repo_icons (the edit-dialog picker)
# ---------------------------------------------------------------------------

def test_scan_returns_ranked_previews(tmp_path):
    (tmp_path / "favicon.png").write_bytes(_png_bytes())
    (tmp_path / "frontend" / "public").mkdir(parents=True)
    (tmp_path / "frontend" / "public" / "logo.png").write_bytes(_png_bytes(color=(0, 0, 255, 255)))

    res = pi.scan_repo_icons(str(tmp_path))
    names = [c["name"] for c in res]
    assert "favicon.png" in names and "logo.png" in names
    # shallowest first: root favicon.png (depth 0) before frontend/public/logo.png (depth 2)
    assert names.index("favicon.png") < names.index("logo.png")
    assert all(c["image"].startswith("data:image/") for c in res)
    assert all("rel_path" in c and "depth" in c for c in res)


def test_scan_dedupes_identical_content(tmp_path):
    same = _png_bytes(color=(10, 20, 30, 255))
    (tmp_path / "favicon.png").write_bytes(same)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(same)  # identical bytes
    res = pi.scan_repo_icons(str(tmp_path))
    assert len(res) == 1  # deduped by normalized content


def test_scan_empty_repo(tmp_path):
    assert pi.scan_repo_icons(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Umbrella downward scan
# ---------------------------------------------------------------------------

def test_scan_finds_single_git_at_depth_1(tmp_path):
    (tmp_path / "app" / ".git").mkdir(parents=True)
    assert pi.find_single_git_below(str(tmp_path)) == str(tmp_path / "app")


def test_scan_finds_single_git_at_depth_2(tmp_path):
    (tmp_path / "a" / "b" / ".git").mkdir(parents=True)
    assert pi.find_single_git_below(str(tmp_path)) == str(tmp_path / "a" / "b")


def test_scan_ignores_git_beyond_depth_2(tmp_path):
    (tmp_path / "a" / "b" / "c" / ".git").mkdir(parents=True)
    assert pi.find_single_git_below(str(tmp_path)) is None


def test_scan_ambiguous_returns_none(tmp_path):
    (tmp_path / "app" / ".git").mkdir(parents=True)
    (tmp_path / "tools" / ".git").mkdir(parents=True)
    assert pi.find_single_git_below(str(tmp_path)) is None


def test_scan_skips_heavy_dirs(tmp_path):
    # A .git buried under node_modules must not count.
    (tmp_path / "node_modules" / "pkg" / ".git").mkdir(parents=True)
    assert pi.find_single_git_below(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Resolution bricks (own override URL + auto repo URL) — cache-driven, no DB
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_cache():
    pi._repo_icon_states.clear()
    yield
    pi._repo_icon_states.clear()


def _proj(**kw):
    return SimpleNamespace(id=kw.get("id", "-p"), icon=kw.get("icon", "inherit"),
                           icon_anchor=kw.get("icon_anchor"), git_root=kw.get("git_root"))


def test_own_url_none_for_inherit_and_none():
    assert pi.project_own_icon_url(_proj(icon="inherit")) is None
    assert pi.project_own_icon_url(_proj(icon="none")) is None


def test_own_url_for_override_token():
    url = pi.project_own_icon_url(_proj(id="-x", icon="icon-ef.png"))
    assert url == f"/project-icons/{pi._proj_bucket('-x')}/icon-ef.png"


def test_repo_url_uses_anchor_cache():
    pi._repo_icon_states[pi._repo_bucket("/repo")] = "icon-ab.png"
    url = pi.project_repo_icon_url(_proj(git_root="/repo"))
    assert url == f"/project-icons/{pi._repo_bucket('/repo')}/icon-ab.png"


def test_repo_url_none_without_anchor_or_icon():
    assert pi.project_repo_icon_url(_proj(git_root=None)) is None
    assert pi.project_repo_icon_url(_proj(git_root="/repo")) is None  # cache empty


def test_repo_url_prefers_icon_anchor_over_git_root():
    pi._repo_icon_states[pi._repo_bucket("/main")] = "icon-cd.png"
    url = pi.project_repo_icon_url(_proj(git_root="/wt", icon_anchor="/main"))
    assert url == f"/project-icons/{pi._repo_bucket('/main')}/icon-cd.png"


# ---------------------------------------------------------------------------
# Repo-icon discovery + manifest stickiness (filesystem)
# ---------------------------------------------------------------------------

@pytest.fixture
def icons_dir(tmp_path, monkeypatch):
    d = tmp_path / "project-icons"
    d.mkdir()
    monkeypatch.setattr(pi, "get_project_icons_dir", lambda: d)
    return d


def test_discover_writes_manifest_and_is_sticky(tmp_path, icons_dir):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "favicon.png").write_bytes(_png_bytes())

    assert pi._ensure_repo_icon_discovered(str(repo)) is True
    token = pi._repo_icon_states[pi._repo_bucket(str(repo))]
    assert token and token.startswith("icon-")

    # Sticky: a second call does nothing new even if the source vanishes.
    (repo / "favicon.png").unlink()
    pi._repo_icon_states.clear()  # simulate a restart: reload from manifest
    assert pi._ensure_repo_icon_discovered(str(repo)) is False
    assert pi._repo_icon_states[pi._repo_bucket(str(repo))] == token


def test_empty_repo_rescans_until_found(tmp_path, icons_dir):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Nothing found: transient negative, no manifest persisted.
    assert pi._ensure_repo_icon_discovered(str(repo)) is False
    assert pi._repo_icon_states[pi._repo_bucket(str(repo))] is None
    assert pi._read_manifest(icons_dir / pi._repo_bucket(str(repo))) is None

    # Restart + a favicon added later -> now discovered.
    pi._repo_icon_states.clear()
    (repo / "favicon.png").write_bytes(_png_bytes())
    assert pi._ensure_repo_icon_discovered(str(repo)) is True


def test_load_cache_reads_found_icons(tmp_path, icons_dir):
    repo_a = tmp_path / "a"
    repo_a.mkdir()
    (repo_a / "favicon.png").write_bytes(_png_bytes())
    pi._ensure_repo_icon_discovered(str(repo_a))

    pi._repo_icon_states.clear()
    pi.load_repo_icon_cache()
    assert pi._repo_icon_states[pi._repo_bucket(str(repo_a))]  # token string loaded


# ---------------------------------------------------------------------------
# _ensure_project_icon + serialize_project.icon_url (DB-backed)
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_ensure_git_project_discovers_and_serializes(tmp_path, icons_dir):
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "favicon.png").write_bytes(_png_bytes())
    p = Project.objects.create(id="-i-git", directory=str(repo), git_root=str(repo))

    anchor, changed = _ensure(p)
    assert anchor == str(repo) and changed is True
    assert p.icon_anchor is None  # anchor == own git_root -> not materialized
    assert serialize_project(p)["repo_icon_url"] is not None


@pytest.mark.django_db(transaction=True)
def test_worktree_inherits_main_repo_icon(tmp_path, icons_dir):
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "favicon.png").write_bytes(_png_bytes())
    main = Project.objects.create(id="-i-main", directory=str(repo), git_root=str(repo))
    wt = Project.objects.create(
        id="-i-wt", directory=str(tmp_path / "wt"), git_root=str(tmp_path / "wt"), worktree_of=main
    )

    _ensure(main)
    anchor, _ = _ensure(wt)
    assert anchor == str(repo)
    wt.refresh_from_db()
    assert wt.icon_anchor == str(repo)  # materialized: differs from wt.git_root
    assert serialize_project(wt)["repo_icon_url"] is not None


@pytest.mark.django_db(transaction=True)
def test_umbrella_project_anchors_to_single_git_below(tmp_path, icons_dir):
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    umbrella = tmp_path / "umbrella"
    (umbrella / "app" / ".git").mkdir(parents=True)
    (umbrella / "app" / "favicon.png").write_bytes(_png_bytes())
    p = Project.objects.create(id="-i-umb", directory=str(umbrella), git_root=None)

    anchor, changed = _ensure(p)
    assert anchor == str(umbrella / "app") and changed is True
    p.refresh_from_db()
    assert p.icon_anchor == str(umbrella / "app")
    assert serialize_project(p)["repo_icon_url"] is not None


@pytest.mark.django_db(transaction=True)
def test_project_override_and_state_transitions(tmp_path, icons_dir):
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "favicon.png").write_bytes(_png_bytes())
    p = Project.objects.create(id="-i-ovr", directory=str(repo), git_root=str(repo))
    _ensure(p)  # repo icon discovered; p stays "inherit"

    # Per-project override -> exposed as icon_override_url (repo_icon_url stays).
    token = pi.set_project_icon_override_sync("-i-ovr", _png_bytes(color=(0, 255, 0, 255)), ".png")
    assert token
    p.refresh_from_db()
    assert p.icon == token
    ser = serialize_project(p)
    assert ser["icon_override_url"] == f"/project-icons/{pi._proj_bucket('-i-ovr')}/{token}"
    assert ser["repo_icon_url"] is not None

    # "Use color instead" -> none: no own override.
    pi.set_project_icon_state_sync("-i-ovr", pi.ICON_NONE)
    p.refresh_from_db()
    ser = serialize_project(p)
    assert ser["icon"] == "none"
    assert ser["icon_override_url"] is None

    # "Follow inherited" -> inherit: no own override, repo icon still available.
    pi.set_project_icon_state_sync("-i-ovr", pi.ICON_INHERIT)
    p.refresh_from_db()
    ser = serialize_project(p)
    assert ser["icon_override_url"] is None
    assert ser["repo_icon_url"].startswith(f"/project-icons/{pi._repo_bucket(str(repo))}/")
