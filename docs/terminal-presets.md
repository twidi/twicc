# Terminal Presets (`.twicc-tmux.json`)

Define preset shell sessions for the terminal's tmux navigator by placing a `.twicc-tmux.json` file at the root of your project directory.

## Format

The file is a JSON array of preset objects:

| Field     | Type   | Required | Description                                              |
|-----------|--------|----------|----------------------------------------------------------|
| `name`    | string | **yes**  | Display name, also used as the tmux window name          |
| `command` | string | no       | Command to run automatically when the window is created  |
| `cwd`     | string | no       | Working directory (absolute, or relative to project root) |

## Example

```json
[
  { "name": "dev", "cwd": "./frontend", "command": "npm run dev" },
  { "name": "logs", "command": "tail -f logs/backend.log" },
  { "name": "shell" }
]
```

## Behavior

- Presets appear in the terminal's **shell navigator** (the full-page picker) and are prefixed with a ▶ icon in the **tab bar** (desktop) and **dropdown** (mobile).
- A preset that is currently running shows a green ▶ icon in the navigator. Clicking it switches to the existing window instead of creating a duplicate.
- When a preset window is created, its `command` (if any) is sent as keystrokes to the new tmux window.
- Relative `cwd` paths are resolved against the project directory.
- The file is re-read every time the window list is refreshed, so changes take effect without restarting the server.

## Implementation

Loaded by `load_tmux_presets()` in `src/twicc/terminal.py`.
