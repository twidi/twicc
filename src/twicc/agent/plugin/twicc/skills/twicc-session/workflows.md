# `session <SESSION_ID> workflows` / `workflow` — workflow runs

List a session's workflow runs, or show one (Claude Code only). MCP tools: `mcp__twicc__session_workflows`, `mcp__twicc__session_workflow`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

### Workflows — list runs

```bash
$TWICC session <SESSION_ID> workflows [--limit N] [--offset N] [--paginated] [--result] [--full]
```

This session's workflow runs, newest first (**Claude Code** only; the session's `has_workflows` says whether any exist). Each row: `id`, `workflowName`, `summary`, `status`, `statusKind`, `startTime`, `durationMs`, `agentCount`, `totalTokens`, `totalToolCalls`, `phases`, `phaseCompletion`, `scriptPath`, `defaultModel`.

- `--result` — adds each run's `result`: to read conclusions, not to choose a run.
- `--full` — the whole envelope, execution trace included (`workflowProgress`, `script`, `logs`, `args`, `result`). **Can be megabytes** (a prompt and a result preview per agent). For one run, use `workflow <ID>`.

### Workflow — one run

```bash
$TWICC session <SESSION_ID> workflow <ID>
```

One run, by its `id` from `workflows`, in the `--full` shape. Exit 1 when unknown.

## Examples

```bash
$TWICC session abc123 workflows
$TWICC session abc123 workflows --limit 5
$TWICC session abc123 workflow wf_cd590ff1
```

## Related commands

- `$TWICC session <ID>` — `has_workflows` says whether the session has any. File: `row.md`.

## How to present results

1. List runs with their name, status and duration; summarize `result` only when you fetched it.
