# `create-session --worktree-*` — land the session in a git worktree

Create a new git worktree of `--project`, or adopt an existing one, and open the session in it, in one command — the CLI counterpart of the UI's "new worktree" button. MCP tool: `mcp__twicc__create_session`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC create-session --project <SOURCE_REPO> --worktree-path <ABS_PATH> [--worktree-branch BRANCH [--worktree-start-from REF]] '<PROMPT>'
```

- `--worktree-branch BRANCH` — create the session in a NEW git worktree of `--project` on `BRANCH` (an existing local branch is checked out; a new one is created with `-b`). `--project` then names the **source repository**. The session lands in the worktree, registered as its own project linked back to the source (`worktree_of`); it inherits the source's agent defaults and trust. Requires `--worktree-path`.
- `--worktree-path PATH` — absolute path of the git worktree's directory.
  - **With `--worktree-branch`**: where the NEW worktree is created (git rejects a non-empty target).
  - **Without `--worktree-branch`**: an EXISTING worktree of `--project` to **adopt** — registered as its own project linked via `worktree_of` (no `git worktree add`), so the session opens in it. Adoption is idempotent (an already-registered worktree is reused). The path must be a real linked worktree of `--project`, not an arbitrary directory.
- `--worktree-start-from REF` — branch/revision the new branch starts from, only when `--worktree-branch` does not yet exist. Default: the source repo's current HEAD. Ignored for an existing-branch checkout. Only valid with `--worktree-branch`.

The agent-settings flags still resolve against `--project` (the source): the worktree inherits from it, so the effective values match a UI draft opened in the worktree (file: `agent-settings.md`).

## Errors

Local (exit 1):

- `missing_worktree_branch` — `--worktree-start-from` without `--worktree-branch` (start-from only shapes new-branch creation).
- `missing_worktree_path` — `--worktree-branch` without `--worktree-path`.
- `invalid_worktree_path` — `--worktree-path` must be an absolute path.

Server (exit 3):

- `not_git_repo` — a worktree flag was used but `--project` is not a git repository.
- `not_a_worktree` — `--worktree-path` (adoption, no `--worktree-branch`) is not a real linked worktree of `--project`, or its directory is gone.
- `project_already_exists` — a project is already registered for the new `--worktree-path` (creation only; adoption reuses it instead).
- `start_from_not_found` — `--worktree-start-from` is not an existing local branch.
- `git_error` — `git worktree add` failed (branch already checked out, non-empty target, …); the git message is relayed verbatim.

## Examples

```bash
$TWICC create-session --project /home/twidi/dev/myproj --worktree-branch feature/login --worktree-path /home/twidi/dev/myproj.worktrees/feature-login 'Implement the login flow'
```

## Related commands

- `$TWICC project <project_id>` — the worktree's project, and its `worktree_of` link. Skill: `twicc-project`.
