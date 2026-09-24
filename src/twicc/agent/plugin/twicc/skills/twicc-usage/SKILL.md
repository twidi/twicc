---
name: twicc-usage
description: Show API usage quotas and cost estimates per backend provider. Use when you or the user want to check utilization, rate limits, or spending.
---

# TwiCC Usage

Show the latest usage quota snapshot with cost estimates for every provider.

## When to use

- You or the user want to check current API usage, quota utilization, or spending.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC usage
```

No options.

## Output format

JSON object keyed by provider:

```json
{
  "claude_code": {
    "provider": "claude_code",
    "fetched_at": "2026-05-31T07:51:20+00:00",
    "five_hour_utilization": 2.0,
    "five_hour_resets_at": "2026-05-31T12:20:00+00:00",
    "seven_day_utilization": 38.0,
    "seven_day_resets_at": "2026-06-04T19:00:00+00:00",
    "extra_usage_is_enabled": true,
    "extra_usage_monthly_limit": 6000,
    "extra_usage_used_credits": 512.0,
    "extra_usage_utilization": 8.5,
    "extra_usage_remaining_credits": null,
    "period_costs": {
      "five_hour": {"spent": 3.19, "estimated_period": 27.59, "estimated_monthly": 3973.0, "capped": false, "cutoff_at": null},
      "seven_day": {"spent": 721.30, "estimated_period": 1898.15, "estimated_monthly": 8134.95, "capped": true, "cutoff_at": "2026-06-04T11:17:42+00:00"}
    },
    "five_hour_temporal_pct": 10.4,
    "five_hour_burn_rate": 19.2,
    "five_hour_started_at": "2026-05-31T07:20:00+00:00",
    "seven_day_temporal_pct": 36.2,
    "seven_day_burn_rate": 104.9,
    "seven_day_started_at": "2026-05-28T19:00:00+00:00",
    "five_hour_burn_rate_30min": 95.4,
    "five_hour_burn_rate_1h": 87.1,
    "seven_day_burn_rate_12h": 132.0,
    "seven_day_burn_rate_24h": 118.4
  }
}
```

### Key fields

- `five_hour_*` / `seven_day_*` — 5-hour and 7-day rolling window quotas.
- `*_utilization` — percentage of quota used (e.g. 45.0 = 45%).
- `*_temporal_pct` — percentage of time elapsed in the quota window.
- `*_burn_rate` — utilization vs. time elapsed since window start; >100 means on track to exhaust before reset.
- `five_hour_burn_rate_30min` / `five_hour_burn_rate_1h` / `seven_day_burn_rate_12h` / `seven_day_burn_rate_24h` — burn rate over the trailing lookback (same scale as `*_burn_rate`); `null` when not computable.
- `*_resets_at` — when the quota window resets (ISO 8601).
- `extra_usage_*` — extra usage billing (if enabled). For Claude code, credits are in cents of the user currency, and uses monthly limit, used credits and utilization. Codex only uses `remaining_credits`.
- `period_costs.*.spent` — actual cost in USD so far.
- `period_costs.*.estimated_monthly` — projected monthly cost at current pace.
- `period_costs.*.capped` — `true` if the estimate was capped (quota exhausted before period end).
- `period_costs.*.cutoff_at` — when the cap was hit (ISO 8601), or `null`.

## Examples

```bash
$TWICC usage
$TWICC usage | jq '.claude_code.five_hour_utilization'
$TWICC usage | jq 'map_values({five_hour_burn_rate, seven_day_burn_rate})'
```

## Related commands

- `$TWICC info` — the registered providers and which ones are disabled. Skill: `twicc-info`.

## How to present results

1. Summarize per provider; focus on 5-hour and 7-day quotas.
2. Show utilization as percentages; highlight if above 80%.
3. Use burn rate to assess risk: >100 means quota will be exhausted before reset.
4. Show reset times in human-readable format.
5. Include cost estimates only when asked.
6. For Claude Code, redirect to https://claude.ai/settings/usage for more detail.
