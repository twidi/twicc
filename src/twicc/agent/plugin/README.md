# TwiCC Agent Plugin

This directory contains the TwiCC plugin for Claude Code agents. The plugin exposes a set of **skills** — structured instruction files that tell an agent how to use TwiCC's CLI to query sessions, projects, workspaces, processes, and more.

## Structure

```
twicc/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest (name, version, skills path)
└── skills/
    ├── twicc-create-session/
    │   └── SKILL.md
    ├── twicc-send-message/
    │   └── SKILL.md
    ├── twicc-session/       # A split skill (see "Split skills")
    │   ├── SKILL.md         # Index
    │   ├── messages.md      # One file per sub-command
    │   └── ...
    └── ...                  # One subdirectory per skill
```

The `version` field in `plugin.json` **must be bumped on every change** to the skill bundle, so providers (Claude Code, Codex, …) know to refresh their local copy:

- Any user-visible skill change (body text, wording) → bump the **patch** (`0.20.1` → `0.20.2`).
- New skill, or existing skill with new flags/options → bump the **minor** (`0.20.0` → `0.21.0`).
- Skill renamed or removed → bump the **minor** at minimum.

---

## How to write skills

The rules below are derived from the full skill set written for TwiCC. **Read several existing skills before writing a new one** — they are the canonical reference for tone, structure, and level of detail.

### Section order

Every single-file skill follows this order (omit sections that don't apply). A skill may add a section of its own when its content needs one (e.g. `### Delivery timing` under `## Usage` in the sending skills). A split skill follows the orders given in "Split skills" below.

1. Frontmatter (YAML)
2. H1 title
3. One-paragraph lead
4. `## When to use`
5. `## How to invoke` (TWICC resolver block — identical in every skill)
6. `## Usage`
7. `## Errors` (write commands only)
8. `## Output format`
9. `## Examples`
10. `## Following up` (only for commands that spawn async work)
11. `## Related commands`
12. `## How to present results`

### Frontmatter

```yaml
---
name: twicc-xxx
description: One-line summary used by the skill system to select this skill. Keep it under ~200 characters.
argument-hint: <required_arg> [optional_arg]   # omit if no arguments
---
```

The `description` is what an agent reads to decide whether to invoke the skill. Make it specific and action-oriented. Use "you or the user" rather than "the user" — agents can invoke skills autonomously, not only on explicit user request.

### Lead paragraph

One short paragraph. What the command does, and any non-obvious constraint (e.g. "one project per directory"). No implementation details (`os.path.realpath`, internal function names, drop-file mechanics, etc.).

### When to use

Bullet list of triggers. Use "You or the user want…" instead of "The user asks…" — this allows autonomous agent use.

### How to invoke

**Identical in every skill** — copy verbatim. It opens with the MCP-preference note (agents
inside a session get the same commands as `mcp__twicc__*` tools, most of them deferred, so a
tool absent from the visible list must be searched for, not assumed missing), then the
`$TWICC` resolver:

```markdown
**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.
```

Do **not** add a "Prerequisite: the server must be running" section — it adds noise without value.

### Usage

```markdown
### Arguments

- `ARG` — description.

### Options

- `--flag VALUE` — description.
```

Use **bullet lists**, never tables, for both options and exit codes.

For multi-subcommand skills in a single file, use `### subcommand-name` subsections, each with its own bash block. A split skill gives each sub-command its own file instead.

### Errors

Only for **write commands** (create, update, delete, send). Read-only commands go straight to `## Output format`.

```markdown
## Errors

### Local (exit 1)

- `error_code` — description only when the name is not self-explanatory.
- `self_explanatory_code`

### Server (exit 3)

Same codes, re-checked server-side. [Plus any server-specific codes.]
```

**When to add a description to an error code:**
- Self-explanatory (`invalid_color`, `duplicate_name`, `project_already_exists`) → no description.
- Adds non-obvious info (character limit, which flag causes it, what action unblocks it) → one-line description.

Common codes that always get a description: `is_subagent` (explain where to target instead), `awaiting_user_input` (explain that the user must click in the UI).

### Output format

**Show the JSON output directly.** The CLI speaks JSON by default on every structured command — there is no text mode and no flag to pass. Agents parse the JSON.

For commands with multiple output shapes (listing vs. `get`, or per-subcommand), use subsections. Keep JSON examples realistic and complete but not padded.

**Exit codes** as a bullet list:

```markdown
### Exit codes

- `0` — Success
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
```

### Examples

Real bash invocations, no prose comments. A trailing `# → …` comment is fine for JSON output. Remove verbose comments like `# Simplest:` or `# Machine-parseable output for scripts.` — the code speaks for itself.

### Related commands

```markdown
- `$TWICC some-command <ARGS>` — one-line description. Skill: `twicc-some-command`.
```

- List the skill name after every entry (agents use it to invoke the right skill).
- When two entries point to the same skill, list the skill name only on the first occurrence.
- Keep descriptions to one clause — not a full sentence with a period trail.

### How to present results

Numbered list. Concise. End with the TwiCC link pattern when applicable:

```
You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
```

---

## Split skills

An agent pays for the whole `SKILL.md` each time it loads the skill. A big skill therefore splits into a short **index** `SKILL.md` and **one file per sub-command** next to it; the agent reads only the file it needs. Reference: `twicc-session`.

### When to split

- The skill has sub-commands, or independent topics (`twicc-info` sections, `twicc-create-session` option groups).
- **And** a single `SKILL.md` would exceed ~16 000 characters (~4 500 tokens). Below that, keep one file.

Split skills today: `twicc-session`, `twicc-sessions`, `twicc-info`, `twicc-create-session`, `twicc-update-session`, `twicc-update-sessions`.

### The index `SKILL.md`

1. Frontmatter — `description` still lists every capability: it drives the skill's selection.
2. H1 title
3. One-sentence lead that says the file is the index
4. `## When to use`
5. `## How to invoke` (identical in every skill)
6. `## Common to all sub-commands` — only what really is common: the id argument, shared flags (`--timeout`), the selection model, and the output, exit codes and errors when they are the same for every sub-command
7. `## Sub-commands` — the sentence **"ALWAYS READ THE SUB-COMMAND'S FILE BEFORE YOU CALL IT."**, then a table `Sub-command | Purpose | File` (one line of purpose each)
8. `## Related commands` — for the skill as a whole

Adapt to the unit when the split is not by sub-command:

- `twicc-info` (split by section): "section" in the headings and the sentence; the index also keeps the examples and presentation rules that span several sections.
- `twicc-create-session` (split by topic): the index keeps the base command in the single-file order (`Usage`, `Output format`, `Errors`, `Examples`), then `## Topics`, `Related commands`, `How to present results`.

The sentence after the capitals says what the file holds and what stays in the index — never claim the index holds nothing when it holds common rules.

### A sub-command file

- **Name:** the sub-command, lowercase (`wait-reply.md`). A pair that toggles one flag shares one file (`archive.md` for `archive` / `unarchive`). The default action of a command without a sub-command name gets a descriptive name (`row.md`, `list.md`).
- **Header:** `# \`<command signature>\` — <short purpose>`, then one line: the purpose, `MCP tool: \`mcp__twicc__...\``, and "Read `SKILL.md` first: it resolves `$TWICC`."
- **Sections**, in this order, omitting the empty ones: `## Usage`, `## Output format` (with `### Exit codes` when specific), `## Errors` when specific, the skill's own sections, `## Examples`, `## Related commands`, `## How to present results`. No `When to use` or `How to invoke`: the index holds them.
- **Self-contained:** each file carries its own pitfalls, examples, related commands (sibling files as "File: `x.md`", other skills as "Skill: `twicc-x`") and presentation rules. Say a rule once in the index or once in a file; point at it rather than repeat it.

### Checks after a change

- The documentation tests (`tests/test_*_documentation.py`) scan **every** `*.md` of every skill, so a flag written in a sub-command file is checked like one in a `SKILL.md`. A test that pins a text reads the file that now holds it.
- Rewording while moving text loses facts in ways that read naturally: a rule narrowed or broadened, a qualifier dropped ("only", "oldest", "X included"), the condition of one field leaking onto its neighbour when two sentences merge. Compare the result with the previous version, fact by fact, before shipping it.
- Avoid position words about transcript lines ("above", "below", "lower"): the CLI uses them in both directions. Write "before" / "past".

---

## What to leave out

| Avoid | Why |
|---|---|
| "Prerequisite: the server must be running" section | Adds noise; the exit code table already covers it |
| Python scripting examples (`zip`, `subprocess`) | Agents read JSON directly |
| Internal implementation details | `os.path.realpath`, `path_to_project_id`, drop-file mechanics — agents don't need these |
| Background behavior descriptions | What the server does internally after a command (re-indexing, broadcasting, tmux teardown…) — unless it directly affects how the agent should call or follow up the command |
| Verbose `--spawned-by self` explanation | Just say "`self` means the current session" |
| Long inline cross-references to `twicc-info` | One sentence max; the skill itself has the details |
| Tautological field descriptions | If the field name says it all, omit the description |

### Keep descriptions short

Every description — argument, option, field, error code, bullet point — should fit in **one sentence**. If you need a second sentence, ask whether the second sentence is truly necessary for the agent to call the command correctly.

Symptoms of over-writing to watch for:

- An argument description that explains how the resolution algorithm works internally, rather than what the agent should pass.
- An option description that lists every sub-case and exception instead of stating the main behavior.
- An error code description that paraphrases the code name and then adds a paragraph of context.
- A "When to use" bullet that describes a complete workflow instead of a single trigger.

When in doubt: trim, then check whether anything essential was lost. If not, the trimmed version is better.

---

## Target lengths

| Skill type | Target |
|---|---|
| Read-only, single action | ~40–60 lines |
| Write command (create/update/delete) | ~80–120 lines |
| Multi-subcommand | ~120–160 lines |
| Split skill: index `SKILL.md` | ~50–110 lines |
| Split skill: one sub-command file | ~25–90 lines |

These are soft targets. Precision matters more than brevity — never drop information that an agent needs to call the command correctly. But if you find yourself writing more than 200 lines, look for implementation details, repeated prose, or tables that can be collapsed into lists.
