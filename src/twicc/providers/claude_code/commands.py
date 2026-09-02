"""
Command discovery from the filesystem.

Scans user-level, project-level, and plugin sources for both legacy commands
(.claude/commands/*.md) and skills (.claude/skills/<name>/SKILL.md).
Only returns commands available in non-interactive mode (SDK-compatible).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

from .workflow_meta import WorkflowMetaError, extract_workflow_meta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML frontmatter parser (no PyYAML dependency — handles simple key: value)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?([\s\S]*)", re.MULTILINE)
_YAML_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)")


def _parse_yaml_value(raw: str) -> str | bool | None:
    """Parse a simple YAML scalar value."""
    raw = raw.strip()
    if not raw:
        return None
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "~"):
        return None
    return raw


def _parse_simple_yaml(text: str) -> dict[str, str | bool | list[str] | None]:
    """Parse simple YAML (single-level key: value, with optional list values)."""
    result: dict[str, str | bool | list[str] | None] = {}
    lines = text.split("\n")
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in lines:
        list_match = _YAML_LIST_ITEM_RE.match(line)
        if list_match and current_key is not None:
            if current_list is None:
                current_list = []
            current_list.append(list_match.group(1).strip().strip("'\""))
            continue

        if current_key is not None and current_list is not None:
            result[current_key] = current_list
            current_list = None
            current_key = None

        colon_pos = line.find(":")
        if colon_pos > 0 and not line[:colon_pos].startswith(" "):
            key = line[:colon_pos].strip()
            value_part = line[colon_pos + 1:].strip()
            current_key = key
            if value_part:
                result[key] = _parse_yaml_value(value_part)
            else:
                result[key] = None

    if current_key is not None and current_list is not None:
        result[current_key] = current_list

    return result


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown content."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    fm = _parse_simple_yaml(match.group(1))
    return fm, match.group(2)


# ---------------------------------------------------------------------------
# Field extraction helpers (matching the CLI's behavior)
# ---------------------------------------------------------------------------


def _extract_description(body: str, fallback: str = "Custom command") -> str:
    """Extract description from the first non-empty line, stripping markdown heading prefix."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped:
            heading_match = re.match(r"^#+\s+(.+)$", stripped)
            desc = heading_match.group(1) if heading_match else stripped
            return desc[:97] + "…" if len(desc) > 100 else desc
    return fallback


def _is_user_invocable(fm: dict) -> bool:
    """Check if the command/skill is user-invocable (defaults to True if absent)."""
    val = fm.get("user-invocable")
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


# ---------------------------------------------------------------------------
# Discovered command data
# ---------------------------------------------------------------------------


class DiscoveredCommand(NamedTuple):
    """A command discovered from the filesystem."""
    name: str
    plugin_name: str | None
    description: str
    argument_hint: str | None
    # True for saved workflows (``.claude/workflows/*.js``); False for legacy
    # commands and skills. Preserved across ``_replace(plugin_name=...)``.
    is_workflow: bool = False


# ---------------------------------------------------------------------------
# Directory scanners
# ---------------------------------------------------------------------------


def _scan_commands_dir(directory: Path) -> list[DiscoveredCommand]:
    """Recursively scan for *.md files in a legacy commands directory."""
    results: list[DiscoveredCommand] = []
    if not directory.is_dir():
        return results

    for md_file in sorted(directory.rglob("*.md")):
        rel = md_file.relative_to(directory)
        parts = list(rel.parts)
        name_part = parts[-1].removesuffix(".md")
        cmd_name = ":".join([*parts[:-1], name_part])

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm, body = _parse_frontmatter(content)

        if not _is_user_invocable(fm):
            continue

        description = fm.get("description")
        if not description or not isinstance(description, str):
            description = _extract_description(body, "Custom command")

        argument_hint = fm.get("argument-hint")
        if argument_hint is not None:
            argument_hint = str(argument_hint)

        results.append(DiscoveredCommand(
            name=cmd_name,
            plugin_name=None,
            description=description,
            argument_hint=argument_hint,
        ))

    return results


def _scan_skills_dir(directory: Path) -> list[DiscoveredCommand]:
    """Scan for immediate subdirectories containing SKILL.md."""
    results: list[DiscoveredCommand] = []
    if not directory.is_dir():
        return results

    for subdir in sorted(directory.iterdir()):
        if not subdir.is_dir():
            continue

        skill_file: Path | None = None
        for candidate in subdir.iterdir():
            if candidate.is_file() and candidate.name.lower() == "skill.md":
                skill_file = candidate
                break

        if skill_file is None:
            continue

        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm, body = _parse_frontmatter(content)

        if not _is_user_invocable(fm):
            continue

        skill_name = fm.get("name") if isinstance(fm.get("name"), str) else subdir.name

        description = fm.get("description")
        if not description or not isinstance(description, str):
            description = _extract_description(body, "Skill")

        argument_hint = fm.get("argument-hint")
        if argument_hint is not None:
            argument_hint = str(argument_hint)

        results.append(DiscoveredCommand(
            name=skill_name,
            plugin_name=None,
            description=description,
            argument_hint=argument_hint,
        ))

    return results


def _scan_workflows_dir(directory: Path) -> list[DiscoveredCommand]:
    """Scan a ``workflows`` directory for saved workflow ``*.js`` files.

    Each file's ``export const meta = {...}`` block is parsed in pure Python
    (no Node). Resilience: a file whose meta can't be extracted — or that
    yields no usable name — is skipped silently, exactly like an unreadable
    command file. The command name is ``meta.name`` when present, else the
    filename stem (mirroring how skills fall back from the frontmatter
    ``name`` to the directory name); the description falls back to
    ``"Workflow"``. Scanned flat (no recursion) — saved workflows live
    directly under ``workflows/``.
    """
    results: list[DiscoveredCommand] = []
    if not directory.is_dir():
        return results

    for js_file in sorted(directory.glob("*.js")):
        if not js_file.is_file():
            continue

        try:
            content = js_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            meta = extract_workflow_meta(content)
        except WorkflowMetaError:
            continue  # not a parseable workflow — ignore

        name = meta.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:  # absent, non-string, or blank → fall back to the filename
            name = js_file.stem.strip()
        if not name:
            continue  # nothing usable to invoke

        description = meta.get("description")
        if not description or not isinstance(description, str):
            description = "Workflow"

        results.append(DiscoveredCommand(
            name=name,
            plugin_name=None,
            description=description,
            argument_hint=None,
            is_workflow=True,
        ))

    return results


def _resolve_plugin_install_path(install_path: Path) -> Path | None:
    """Resolve the actual install path for a plugin, handling stale versions.

    If the install path doesn't exist (plugin was updated), falls back to the
    most recently modified version directory in the parent.
    """
    if install_path.is_dir():
        return install_path
    parent = install_path.parent
    if not parent.is_dir():
        return None
    candidates = sorted(
        (d for d in parent.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _scan_single_skill_dir(skill_dir: Path, plugin_name: str) -> DiscoveredCommand | None:
    """Scan a single directory expected to contain a SKILL.md file.

    Used for manifest-declared skill paths where the directory itself is the skill
    (as opposed to _scan_skills_dir which scans subdirectories).
    """
    if not skill_dir.is_dir():
        return None

    skill_file: Path | None = None
    for candidate in skill_dir.iterdir():
        if candidate.is_file() and candidate.name.lower() == "skill.md":
            skill_file = candidate
            break

    if skill_file is None:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    fm, body = _parse_frontmatter(content)

    if not _is_user_invocable(fm):
        return None

    skill_name = fm.get("name") if isinstance(fm.get("name"), str) else skill_dir.name

    description = fm.get("description")
    if not description or not isinstance(description, str):
        description = _extract_description(body, "Skill")

    argument_hint = fm.get("argument-hint")
    if argument_hint is not None:
        argument_hint = str(argument_hint)

    return DiscoveredCommand(
        name=skill_name,
        plugin_name=plugin_name,
        description=description,
        argument_hint=argument_hint,
    )


def _read_plugin_manifest(install_path: Path) -> dict | None:
    """Read and parse the .claude-plugin/plugin.json manifest."""
    import json

    manifest_path = install_path / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _scan_plugin(plugin_name: str, install_path: Path) -> list[DiscoveredCommand]:
    """Scan a single plugin for commands and skills.

    Discovery strategy:
    1. Read the plugin manifest (.claude-plugin/plugin.json)
    2. If the manifest declares "skills" or "commands" as arrays of paths,
       scan those specific directories (relative to install_path)
    3. Otherwise, fall back to scanning commands/ and skills/ directories
       at the root of the install path
    """
    results: list[DiscoveredCommand] = []
    manifest = _read_plugin_manifest(install_path)

    # Determine which directories to scan for skills
    manifest_skills = manifest.get("skills") if manifest else None
    if isinstance(manifest_skills, list) and manifest_skills:
        # Manifest declares specific skill paths (e.g. ["./.claude/skills/my-skill"])
        for skill_path_str in manifest_skills:
            if not isinstance(skill_path_str, str):
                continue
            skill_dir = (install_path / skill_path_str).resolve()
            cmd = _scan_single_skill_dir(skill_dir, plugin_name)
            if cmd is not None:
                results.append(cmd)
    else:
        # Fallback: scan skills/ directory at plugin root
        skills_dir = install_path / "skills"
        if skills_dir.is_dir():
            for skill in _scan_skills_dir(skills_dir):
                results.append(skill._replace(plugin_name=plugin_name))

    # Determine which directories to scan for commands
    manifest_commands = manifest.get("commands") if manifest else None
    if isinstance(manifest_commands, list) and manifest_commands:
        # Manifest declares specific command paths
        for cmd_path_str in manifest_commands:
            if not isinstance(cmd_path_str, str):
                continue
            cmd_dir = (install_path / cmd_path_str).resolve()
            if cmd_dir.is_dir():
                for cmd in _scan_commands_dir(cmd_dir):
                    results.append(cmd._replace(plugin_name=plugin_name))
    else:
        # Fallback: scan commands/ directory at plugin root
        commands_dir = install_path / "commands"
        if commands_dir.is_dir():
            for cmd in _scan_commands_dir(commands_dir):
                results.append(cmd._replace(plugin_name=plugin_name))

    # Determine which directories to scan for workflows (plugins may ship
    # saved workflows too). Same shape as commands: honour a manifest
    # ``workflows`` array of directory paths, else fall back to a
    # ``workflows/`` directory at the plugin root.
    manifest_workflows = manifest.get("workflows") if manifest else None
    if isinstance(manifest_workflows, list) and manifest_workflows:
        for wf_path_str in manifest_workflows:
            if not isinstance(wf_path_str, str):
                continue
            wf_dir = (install_path / wf_path_str).resolve()
            if wf_dir.is_dir():
                for wf in _scan_workflows_dir(wf_dir):
                    results.append(wf._replace(plugin_name=plugin_name))
    else:
        workflows_dir = install_path / "workflows"
        if workflows_dir.is_dir():
            for wf in _scan_workflows_dir(workflows_dir):
                results.append(wf._replace(plugin_name=plugin_name))

    return results


# ---------------------------------------------------------------------------
# Plugin data structures
# ---------------------------------------------------------------------------


class PluginEntry(NamedTuple):
    """A resolved plugin entry from installed_plugins.json."""
    plugin_name: str
    install_path: Path
    scope: str  # "user", "project", "local", "managed"
    project_path: str | None  # Only set for project/local scoped plugins


def read_plugin_entries() -> list[PluginEntry]:
    """Read and resolve plugin entries from installed_plugins.json."""
    import json

    from twicc.provider_homes import claude_config_dir

    plugins_file = claude_config_dir().path / "plugins" / "installed_plugins.json"
    if not plugins_file.exists():
        return []

    try:
        data = json.loads(plugins_file.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    if data.get("version") != 2:
        return []

    entries: list[PluginEntry] = []

    for key, plugin_entries in data.get("plugins", {}).items():
        plugin_name = key.split("@")[0]

        for entry in plugin_entries:
            raw_path = Path(entry.get("installPath", ""))
            resolved = _resolve_plugin_install_path(raw_path)
            if resolved is None:
                continue

            scope = entry.get("scope", "user")
            project_path = entry.get("projectPath")

            entries.append(PluginEntry(
                plugin_name=plugin_name,
                install_path=resolved,
                scope=scope,
                project_path=project_path,
            ))

    return entries


# ---------------------------------------------------------------------------
# Directory traversal
# ---------------------------------------------------------------------------


def _walk_up_to_home(directory: Path) -> list[Path]:
    """Walk from directory up to HOME (excluded), collecting directories.

    Stops before reaching the home directory itself.
    """
    home = Path.home().resolve()
    dirs: list[Path] = []
    current = directory.resolve()
    while True:
        if current == home:
            break
        dirs.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return dirs


# ---------------------------------------------------------------------------
# High-level discovery
# ---------------------------------------------------------------------------


def discover_global_commands(plugin_entries: list[PluginEntry] | None = None) -> list[DiscoveredCommand]:
    """Discover all global (user-level) commands.

    Includes:
    - User commands from ``<claude home>/commands/``
    - User skills from ``<claude home>/skills/``
    - User workflows from ``<claude home>/workflows/``
    - Plugin commands/skills from user/managed-scoped plugins

    ``<claude home>`` is ``~/.claude`` or the configured ``CLAUDE_CONFIG_DIR``
    (``twicc.provider_homes``); project-level ``<repo>/.claude/`` is unrelated.
    """
    from twicc.provider_homes import claude_config_dir

    home = claude_config_dir().path
    commands: list[DiscoveredCommand] = []

    commands.extend(_scan_commands_dir(home / "commands"))
    commands.extend(_scan_skills_dir(home / "skills"))
    commands.extend(_scan_workflows_dir(home / "workflows"))

    if plugin_entries is None:
        plugin_entries = read_plugin_entries()

    for entry in plugin_entries:
        if entry.scope in ("user", "managed"):
            commands.extend(_scan_plugin(entry.plugin_name, entry.install_path))

    return commands


def discover_project_commands(
    project_directory: str,
    plugin_entries: list[PluginEntry] | None = None,
    scanned_dirs: set[Path] | None = None,
) -> list[DiscoveredCommand]:
    """Discover commands specific to a project.

    Includes:
    - Project commands/skills from .claude/ directories (walking up from project_directory to HOME)
    - Plugin commands/skills from project/local-scoped plugins matching the project directory

    Args:
        project_directory: The project's filesystem directory.
        plugin_entries: Pre-loaded plugin entries (avoids re-reading installed_plugins.json).
        scanned_dirs: Set of already-scanned directories to avoid duplicates across projects.
                      Will be mutated (directories added as they are scanned).
    """
    project_path = Path(project_directory).resolve()
    if not project_path.is_dir():
        return []

    if scanned_dirs is None:
        scanned_dirs = set()

    commands: list[DiscoveredCommand] = []

    # Walk up from project directory to home, scanning .claude/commands/ and .claude/skills/
    for d in _walk_up_to_home(project_path):
        if d in scanned_dirs:
            # Already scanned for another project sharing this ancestor — skip filesystem scan
            # but we still need to check plugins below
            continue
        scanned_dirs.add(d)

        commands.extend(_scan_commands_dir(d / ".claude" / "commands"))
        commands.extend(_scan_skills_dir(d / ".claude" / "skills"))
        commands.extend(_scan_workflows_dir(d / ".claude" / "workflows"))

    # Plugin commands for this project (project/local scoped)
    if plugin_entries is None:
        plugin_entries = read_plugin_entries()

    project_dir_str = str(project_path)
    for entry in plugin_entries:
        if entry.scope in ("project", "local") and entry.project_path == project_dir_str:
            commands.extend(_scan_plugin(entry.plugin_name, entry.install_path))

    return commands
