# Tmux probe recipe — scripting the interactive Claude CLI for empirical tests

How to drive a real, interactive `claude` TUI from a script, observe what it
does (screen, JSONL, hooks), and clean up. This technique produced every
"verified" claim in `docs/plans/2026-06-10-hybrid-claude-cli-mode-design.md`
and is the standard way to test hybrid-mode behaviors. Keep it handy for any
future investigation of CLI behavior.

## Principles

- **Isolation:** dedicated tmux socket (`-L twicc-probe` — never a real
  instance socket: `-L twicc` for `~/.twicc`, `-L twicc-<sha8>` for a worktree,
  see `paths.tmux_socket_suffix`), throwaway cwd (e.g. `/tmp/twicc-cwd-test`),
  `kill-server` before and after each run.
- **One probe = one script**, run in the background (it is full of `sleep`s);
  read its output when it completes. Print everything you will want to assert
  on — pane captures, file listings, JSONL extracts — as you go.
- **Never delete provider JSONL files** (project rule). Probe sessions pollute
  `~/.claude/projects/-tmp-twicc-cwd-test/` harmlessly; leave them.
- **Cost control:** probes consume real API turns on the user's account. Use
  `--model sonnet --effort low` (or the full name `claude-haiku-4-5-20251001`).
  ⚠️ The bare alias `haiku` is SILENTLY IGNORED (falls back to the user's
  default model) — use the full name if you want Haiku; `sonnet`/`opus`/`fable`
  aliases work.

## The skeleton

```bash
#!/bin/bash
BIN=<repo>/.venv/lib/python3.13/site-packages/claude_agent_sdk/_bundled/claude
SOCK=twicc-probe
SID=$(python3 -c 'import uuid; print(uuid.uuid4())')
F="$HOME/.claude/projects/-tmp-twicc-cwd-test/$SID.jsonl"   # cwd → dir name: '/' and '.' become '-'
echo "SESSION_ID=$SID"

# Purge ALL Claude Code env markers by prefix — a fixed -u list is NOT enough
# (see traps below: one missed marker silently disables transcript saving).
UNSETS=$(env | cut -d= -f1 | grep -E '^(CLAUDE_CODE|CLAUDECODE)' | sed 's/^/-u /' | tr '\n' ' ')

tmux -L $SOCK kill-server 2>/dev/null
tmux -L $SOCK new-session -d -s probe -x 200 -y 50 -c /tmp/twicc-cwd-test \
  "exec env $UNSETS $BIN --model claude-haiku-4-5-20251001 --session-id $SID [other flags]"
sleep 12   # TUI warm-up

# Trust dialog (first run in a never-trusted dir): detect and accept.
if tmux -L $SOCK capture-pane -t probe -p | grep -qiE 'trust|proceed'; then
  tmux -L $SOCK send-keys -t probe Enter; sleep 8
fi

# Type a message: bracketed paste (multiline-safe, no premature submit,
# does not trigger the @ file picker), then Enter to submit.
printf '%s' 'Your prompt here. Can be multiline, can contain @/abs/path mentions or /slash commands.' \
  | tmux -L $SOCK load-buffer -
tmux -L $SOCK paste-buffer -p -t probe
sleep 2
tmux -L $SOCK send-keys -t probe Enter
sleep 16   # one short turn (sonnet low / haiku); scale up for longer work

# Observe the screen at any point:
tmux -L $SOCK capture-pane -t probe -p | grep -v '^[[:space:]]*$' | tail -15

# Interact with TUI dialogs (approval prompts, AskUserQuestion, menus):
tmux -L $SOCK send-keys -t probe Enter       # validate highlighted option
tmux -L $SOCK send-keys -t probe Down Enter  # pick the next option
tmux -L $SOCK send-keys -t probe Escape      # interrupt / cancel

# Inspect the JSONL (line types, tool_use/tool_result, state lines):
python3 - "$F" <<'EOF'
import sys, json, collections
c = collections.Counter()
for line in open(sys.argv[1]):
    try: d = json.loads(line)
    except Exception: continue
    c[d.get("type")] += 1
print(dict(c))
EOF

tmux -L $SOCK kill-server 2>/dev/null
echo DONE
```

## Key details and traps

- **`exec env $UNSETS …`** as the pane command: purge EVERY
  `CLAUDE_CODE*`/`CLAUDECODE*` variable by prefix expansion, never a fixed
  list. A probe launched from inside a Claude session inherits ~6 markers,
  and `CLAUDE_CODE_CHILD_SESSION` ALONE makes a CLI ≥ 2.1.171 silently skip
  transcript persistence entirely: no live writes, nothing at a graceful
  `/exit`, `--resume` finds nothing — yet the `ai-title` line still lands, so
  the JSONL EXISTS but holds no content (this false signal caused a full-day
  misdiagnosis on 2026-06-11; regression of the upstream 2.1.170
  inherited-env fix). `exec` makes the pane PID = the claude PID
  (`tmux list-panes -F '#{pane_pid} #{pane_dead}'` then gives liveness;
  `set-option remain-on-exit on` keeps the dead pane inspectable).
- **Pre-minted `--session-id`** makes the JSONL path deterministic — no
  guessing which file the probe produced. Works in interactive mode (verified).
- **`paste-buffer -p`** = bracketed paste. Without `-p`, each newline submits.
  Pasted slash commands ARE interpreted on submit; pasted `@/path` mentions ARE
  resolved (image/file attachment) — both verified.
- **Hooks as observation taps:** pass `--settings /path/to/settings.json`
  (a FILE — avoids all shell-quoting hell) defining command hooks that drop
  their stdin to timestamped files:

  ```json
  {"hooks": {"PermissionRequest": [{"hooks": [{"type": "command",
    "command": "f=/tmp/probe-events/$(date +%s%N)__PermissionRequest.json; cat > \"$f.tmp\" && mv \"$f.tmp\" \"$f\" || true"}]}]}}
  ```

  Verified working events on 2.1.170: `SessionStart`, `SessionEnd`,
  `UserPromptSubmit`, `Stop`, `Notification`, `PermissionRequest`,
  `PreToolUse`, `PostToolUse`. The nanosecond names give you the exact
  ordering/timeline of everything that fired.
- **`--settings` is also the way to force features**: e.g.
  `"fileCheckpointingEnabled": true` (file-history store), `"fastMode"`.
- **Timing:** ~12 s for TUI warm-up, ~15–20 s per short turn, +5–10 s margins
  around dialogs. Always run the whole probe in the background and read the
  output afterwards; never poll interactively with short sleeps from the
  conversation.
- **Filename pitfalls in assertions:** when testing whether the model SEES an
  image/file, never name the file after its content (`orange.png` → the model
  answers "orange" from the path without reading it; verified). Use random
  names (`img_7f3a.png`).
- **`rg` trap when exploring code for probes:** never glue `-r` into combined
  short flags — `rg -rln foo` parses as `--replace=ln` and silently rewrites
  every match as `ln` in the output (this really happened). Write flags
  separately and avoid `-r` entirely.
