# Terminal Presets (`.twicc-tmux.json`)

Define preset shell sessions for the terminal's tmux navigator by placing a `.twicc-tmux.json` file at the root of your project directory.

## Format

The file is a JSON array of preset objects:

| Field         | Type   | Required | Description                                              |
|---------------|--------|----------|----------------------------------------------------------|
| `name`        | string | **yes**  | Display name, also used as the tmux window name          |
| `command`     | string | no       | Command to run automatically when the window is created  |
| `cwd`         | string | no       | Working directory (absolute, or relative to `relative_to` base) |
| `relative_to` | string | no       | Base for relative `cwd`: `preset_dir` (default), `project_dir`, `git_dir`, `session_cwd` |

## Example

```json
[
  { "name": "dev", "cwd": "frontend", "command": "npm run dev" },
  { "name": "logs", "command": "tail -f logs/backend.log" },
  { "name": "shell" }
]
```

### Library preset file (loaded from another directory)

```json
[
  { "name": "Build", "cwd": "src", "relative_to": "project_dir", "command": "make" },
  { "name": "Docs", "cwd": "docs", "relative_to": "git_dir" },
  { "name": "Tool scripts", "cwd": "scripts", "relative_to": "preset_dir" }
]
```

## `relative_to` values

| Value         | Resolves `cwd` relative to              | Default |
|---------------|------------------------------------------|---------|
| `preset_dir`  | Directory containing the preset file     | **yes** |
| `project_dir` | Project directory (from Claude projects) |         |
| `git_dir`     | Git root directory                       |         |
| `session_cwd` | Session's working directory              |         |

If the base directory is not available (e.g., no git root), the preset is shown greyed out and cannot be launched.

## Behavior

- Presets appear in the terminal's **shell navigator** (the full-page picker) and are prefixed with a ▶ icon in the **tab bar** (desktop) and **dropdown** (mobile).
- A preset that is currently running shows a green ▶ icon in the navigator. Clicking it switches to the existing window instead of creating a duplicate.
- When a preset window is created, its `command` (if any) is sent as keystrokes to the new tmux window.
- Without `relative_to`, relative `cwd` paths are resolved against the directory containing the preset file.
- The file is re-read every time the window list is refreshed, so changes take effect without restarting the server.

## Custom preset files

Users can add arbitrary JSON preset files per project via the navigator's **Add preset file** button. These files follow the same format and are stored as references in `~/.twicc/presets/<project_id>.json`. Files already loaded from the project/git/cwd sources are deduplicated.

## Implementation

Loaded by `load_tmux_presets_from_file()` in `src/twicc/terminal.py`. CWD resolution happens in `_resolve_preset_cwd()` using the session context.
