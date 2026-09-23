# Model × effort scores from Artificial Analysis data

**Status:** design approved section by section in chat on 2026-09-23; independent review PASS (round 4); not implemented.
**Date:** 2026-09-23.

## 1. Context

The Agent Settings model picker shows a matrix: one row per model, one column
per reasoning effort, a 0–100 score per cell. Today the score comes from
DeepSWE (datacurve.ai), fetched daily into the `ModelBenchmark` table, and
blends three weighted axes (Capability / Economy / Speed) with a weighted
geometric mean over min-max-normalized values.

Two problems:

- **DeepSWE stopped adding models on 2026-09-01.** Four of the seven current
  models have no data: `claude-fable-5-1`, `claude-opus-5-5`, `gpt-6-sol`,
  `gpt-6-luna`.
- **The formula misbehaves.** Its capability/cost trade-off depends on the
  whole catalogue's range, not on the task. Even with DeepSWE data,
  Capability 60 / Economy 40 / Speed 0 picks `gpt-6-astra` low as the best
  Codex cell.

This design replaces both the data source and the formula. The new model
comes from the "Intelligence Index vs Cost per Task" and "vs Time per Task"
charts of Artificial Analysis: the user sets a target level on the ability
axis; couples near the target and cheap (or fast) win.

**Goal of this iteration:** try the model in real use. The data is a frozen
snapshot shipped in the package. There is no recurring fetch.

## 2. Decisions

| Topic | Decision |
|---|---|
| Data source | Artificial Analysis model pages, snapshot of 2026-09-23 |
| Data delivery | JSON file bundled in the frontend; no backend path |
| Recurring fetch | removed (DeepSWE sync, periodic task, WebSocket push, `ModelBenchmark` table) |
| User controls | Task type (7 types), Task difficulty (0–100), Favor (Cost / Speed), Auto-select best, Default provider only |
| Above the target | no distance penalty ("Free"): only cost or time penalizes a couple that exceeds the target |
| General type | ability = Intelligence Index; cost / time = Terminal-Bench 4.0 |
| Other types | ability and cost / time = mean over the type's evaluations |
| Slider mapping | catalogue range of the chosen type, concave power curve, exponent 0.55 |
| Tolerance | internal constant: 4 Intelligence Index points, scaled per type |
| Score scope | global across providers (see §4.3 for the exact row set) |
| Presets, weight sliders, locks, the "When Capability moves, favor" select | removed |
| Persistence of the controls | in memory only, reset on reload (unchanged behaviour) |
| Attribution | in the help page only: data source, link, snapshot date |

## 3. Data

### 3.1 File

`frontend/src/data/modelBenchmarks.json`, imported statically by
`frontend/src/stores/benchmarks.js`. Written once from the appendix; no
generator script.

```json
{
  "source": "Artificial Analysis",
  "url": "https://artificialanalysis.ai/",
  "retrieved_at": "2026-09-23",
  "rows": [
    {
      "provider": "claude_code",
      "model": "claude-opus-5-5",
      "effort": "high",
      "intelligence_index": 53.58,
      "evals": {
        "terminalbench_4_0": { "score": 0.566, "cost_usd": 5.12, "time_s": 696 },
        "scicode": { "score": 0.604, "cost_usd": 0.0402, "time_s": 9.14 },
        "aa_briefcase": { "score": 1700, "cost_usd": 6.27, "time_s": 786 },
        "gdpval_aa": { "score": 1690, "cost_usd": 1.54, "time_s": 269 },
        "gdp_pdf": { "score": 0.288, "cost_usd": 0.825, "time_s": 41.4 },
        "automationbench_aa": { "score": 0.632, "cost_usd": 0.699, "time_s": 115 },
        "humanitys_last_exam": { "score": 0.556, "cost_usd": 0.104, "time_s": 35.4 },
        "critpt": { "score": 0.309, "cost_usd": 0.53, "time_s": 177 },
        "aa_omniscience": { "score": 40.6, "cost_usd": 0.00853, "time_s": 2.72 },
        "aa_lcr": { "score": 0.827, "cost_usd": 0.592, "time_s": 6.13 }
      }
    }
  ]
}
```

- **Row key:** `provider` + `model` + `effort`. `model` is the registry
  `full_name` (the matrix already joins on `entry.full_name`,
  `frontend/src/utils/agentMatrix.js`). Keys are unique.
- **Every row has all 10 `evals` keys.** A missing value is `null`.
- **Units are Artificial Analysis's native units.** Rates in 0..1; Elo for
  `aa_briefcase` and `gdpval_aa`; an index in −100..100 for `aa_omniscience`.
  Conversion to 0..100 happens in code (§4.1), not in the file.
- **`time_s`** is Artificial Analysis's "time per task": average decode time,
  which excludes time to first token and overhead.

### 3.2 Rows

56 rows, listed in Appendix A:

| Models | Efforts |
|---|---|
| `claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-5.6-luna` | low, medium, high, xhigh, max |
| `gpt-5.5` | medium, high, xhigh |
| `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-4-6` | max |

Not in the file:

- **Models already retired on 2026-09-23** (`retirement_date` in the past):
  `gpt-5.4`, `gpt-5.4-mini`.
- **Couples without detailed evaluations:**
  - `claude-opus-4-7`, `claude-opus-4-6`, `gpt-5.5` low: Artificial Analysis
    only publishes an estimated index, with no evaluation details;
  - `claude-fable-5` low / medium / high / xhigh, `claude-opus-4-8` low /
    medium / high / xhigh, `claude-sonnet-4-6` low / medium / high: Artificial
    Analysis only evaluates these models at max effort;
  - `claude-opus-4-5-20251101`, `claude-sonnet-4-5-20250929`: no effort-level
    entry at all.

These couples render "?", like any couple without data.

**Rule for future snapshots:** include every supported model that is not
already retired on the snapshot date. A model retiring later stays in the file;
§4.3 drops it at runtime once retired.

**Known gap:** `claude-opus-5-5` max has `time_s: null` for every evaluation
(not yet published). This row has no score in Speed mode (§4.4).

## 4. Scoring

All code lives in `frontend/src/utils/benchmarkScores.js` (rewritten).

### 4.1 Ability and cost / time per task type

| Task type (id) | Label | Evaluations |
|---|---|---|
| `general` | General (Intelligence Index) | — (see below) |
| `coding` | Coding & terminal | `terminalbench_4_0`, `scicode` |
| `office` | Office & knowledge work | `aa_briefcase`, `gdpval_aa`, `gdp_pdf` |
| `automation` | Automation & tools | `automationbench_aa` |
| `science` | Science & reasoning | `humanitys_last_exam`, `critpt`, `scicode` |
| `knowledge` | Factual knowledge | `aa_omniscience` |
| `longdocs` | Long documents | `aa_lcr` |

- **`general`:** ability = `intelligence_index`; cost = `terminalbench_4_0.cost_usd`;
  time = `terminalbench_4_0.time_s`.
- **Other types:** ability = `100 × mean(normalized score of each evaluation)`;
  cost = `mean(cost_usd)`; time = `mean(time_s)`, over the same evaluations.
  If any evaluation of the type has a `null` score, the ability is `null`. If any
  has a `null` cost (or time), the cost (or time) is `null`.
- **Normalization to 0..1:** `aa_briefcase` and `gdpval_aa`:
  `(score − 500) / 2000` (Artificial Analysis's own conversion);
  `aa_omniscience`: `(score + 100) / 200`; all others: unchanged.

### 4.2 Target

```
target = min + (max − min) × (difficulty / 100) ^ CURVE        CURVE = 0.55
```

`difficulty` is the slider value, 0..100. `min` and `max` are the lowest and
highest ability of the chosen type over the scoring set (§4.3), counting only
rows whose ability and **cost** are both non-null. Using cost for the range in
Speed mode too keeps the slider mapping identical in both modes.

### 4.3 Scoring set

A row takes part in the range (§4.2), in the tolerance (§4.4) and in the "best"
reference (§4.5) when:

1. its `provider` is in `settings.enabledProviders`
   (`frontend/src/stores/settings.js`); **and**
2. its `model` matches a registry entry of that provider
   (`getProviderHelpers(provider).getModelRegistry()`, matched on `full_name`)
   for which `isModelAvailable(entry)` is true
   (`frontend/src/providers/baseHelpers.js`: enabled and not retired); **and**
3. its `effort` is selectable for that model: it is one of the provider's
   effort choices (`helpers.getFieldChoices('effort')`) and
   `!helpers.isChoiceDisabled('effort', row.effort, { effectiveModel: entry.selected_model })`,
   the same test that enables a matrix cell (`agentMatrix.js`).

This extends the approved "global" decision with runtime filters: a provider
the user disabled, a model that is disabled or retired, or an effort the model
does not support, cannot win and cannot set the scale. With both providers enabled, the score is global
across them.

Rows outside the scoring set have no score ("?").

### 4.4 Penalty

```
tolerance = TOLERANCE_II × (max_type − min_type) / (max_general − min_general)        TOLERANCE_II = 4

penalty = (ability < target ? ((target − ability) / tolerance)² : 0)
        + log2(metric)
```

- `metric` = cost per task (Favor: Cost) or time per task (Favor: Speed).
- `min_type` / `max_type`: the §4.2 range of the chosen type.
  `min_general` / `max_general`: the lowest and highest `intelligence_index`
  over the scoring-set rows whose `intelligence_index` and
  `terminalbench_4_0.cost_usd` are both non-null, whatever the chosen type.
- For `general`, `tolerance` = 4. Being `tolerance` points below the target
  weighs as much as paying (or waiting) twice as much.
- A row is **scorable** when its ability is non-null and its metric is a
  finite number > 0. A non-scorable row has no score.

### 4.5 Score

```
score = Math.round(100 × 2 ^ (min_penalty − penalty))
```

`min_penalty` is the lowest penalty over the scorable rows of the scoring set.
The best couple scores 100; a score of 50 means "one doubling worse". The score
does not depend on what the matrix displays: hidden older models, or providers
not shown in the current matrix (e.g. the single-provider defaults editor).
Same principle as today. Disabled providers are excluded (§4.3).

Auto-select (`findBestCell`, `AgentSettingsPopover.vue`) and the
best-per-provider ring (`agentMatrix.js`) compare the **rounded** score, as
today: cells with the same rounded score are ties, and auto-select takes the
first such cell in matrix order. Only the regression test (§7) ranks by the
unrounded `penalty`.

### 4.6 Module API

`benchmarkScores.js` exports:

- `TASK_TYPES`: ordered list `{ id, label, evals }` (§4.1); `evals` is `null`
  for `general`.
- `EVAL_LABELS`: display name of each evaluation key — `terminalbench_4_0` →
  "Terminal-Bench 4.0", `scicode` → "SciCode", `aa_briefcase` →
  "AA-Briefcase", `gdpval_aa` → "GDPval-AA", `gdp_pdf` → "GDP.pdf",
  `automationbench_aa` → "AutomationBench-AA", `humanitys_last_exam` →
  "Humanity's Last Exam", `critpt` → "CritPt", `aa_omniscience` →
  "AA-Omniscience", `aa_lcr` → "AA-LCR".
- `CURVE`, `TOLERANCE_II`.
- `scoreKey(provider, model, effort)`: unchanged.
- `computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet)`:
  returns `Map(scoreKey → { score, penalty, ability, target, metric })` for the
  scorable rows of the scoring set. `penalty` is the unrounded §4.4 value;
  `score` is the rounded §4.5 value. `isInScoringSet(row) → boolean` implements §4.3; it
  is injected so the function stays pure and testable.
- `typeMetrics(row, taskType)`: `{ ability, cost, time }` for one row and task
  type (§4.1), each possibly `null`. Used by the scoring and by the details.
- `formatBenchmarkDetails(row, scored, taskType)`: the detail rows of §5.3.
  `scored` is the map value above (it supplies `score` and `target`); the cost
  and time rows come from `typeMetrics(row, taskType)`. The name `scored` avoids
  a clash with registry entries (`entry`, §4.3).

### 4.7 Degenerate cases

- Empty scoring set, or no scorable row: `computeBenchmarkScores` returns an
  empty `Map`. Every cell shows "?".
- No row of the scoring set has both ability and cost non-null for the chosen
  type (so §4.2 has no range): return an empty `Map`, in both Cost and Speed
  modes.
- Range of the chosen type equal to 0 (`max == min`): target = `min`, and no
  distance penalty applies to any row.
- Range of `general` equal to 0 (or no `general` row in the scoring set):
  `tolerance = TOLERANCE_II`, unscaled.
- Tolerance equal to 0 (only possible when the type range is 0): covered by the
  rule above; never divide by it.

## 5. Interface

### 5.1 Controls

`AgentSettingsBenchmarkWeights.vue` is replaced by
`frontend/src/components/message/AgentSettingsBenchmarkTask.vue`. The store
`frontend/src/stores/benchmarkWeights.js` is replaced by
`frontend/src/stores/benchmarkTask.js` (store id `benchmarkTask`).

Store state (in memory only):

| Key | Default | Values |
|---|---|---|
| `taskType` | `'general'` | ids of `TASK_TYPES` |
| `difficulty` | `50` | 0..100, integer |
| `favor` | `'cost'` | `'cost'` \| `'speed'` |
| `autoSelectBest` | `false` | boolean |
| `defaultProviderOnly` | `false` | boolean |

Component, top to bottom:

1. **Task type:** visible label "Task type"; `wa-select` (`size="small"`,
   `aria-label="Task type"`) with one `wa-option` per task type (its `label`).
2. **Task difficulty:** visible label "Task difficulty"; `wa-slider` 0..100,
   step 1, `aria-label="Task difficulty"`, no value readout (like today).
3. **Favor:** visible label "Favor"; `wa-radio-group` with `label="Favor"`, its
   own label part visually hidden (an `aria-label` on the host does not name the
   inner radiogroup in Web Awesome 3),
   with two `wa-radio appearance="button"`: "Cost" (`cost`), "Speed" (`speed`).
4. **Auto-select best** and **Default provider only** switches: unchanged
   markup and visibility rules (`showAutoSelect` prop; Default provider only
   visible when Auto-select is on and `providerCount > 1`).
5. The closing `wa-divider`: unchanged.

Props `providerCount` and `showAutoSelect` are kept. Removed: "More controls" /
"Fewer controls", the three weight sliders, locks, presets, the "When
Capability moves, favor" select.

All `wa-*` components used are already imported in `frontend/src/main.js`
(`select`, `option`, `slider`, `radio-group`, `radio`, `switch`).

Importers to update (the component is swapped, usage is unchanged):

- `frontend/src/components/message/AgentSettingsPopover.vue`;
- `frontend/src/components/message/AgentSettingsDefaultsPicker.vue`
  (`:provider-count="1" :show-auto-select="false"`), used by
  `ProviderSettingsSection.vue` and `AgentSettingsPresetsDialog.vue`.

### 5.2 Auto-select

`AgentSettingsPopover.vue` keeps `findBestCell()` and its
`defaultProviderOnly` logic; it reads the new store. The `watch` sources become
`autoSelectBest`, `defaultProviderOnly`, `taskType`, `difficulty`, `favor`
(replacing `display.capability`, `display.economy`, `display.spd`).

### 5.3 Matrix and detail panel

No visual change to the matrix (`AgentSettingsMatrix.vue`, `agentMatrix.js`):
same score, "?", fill, best-per-provider ring, check mark and dots.
`buildMatrixBlocks` keeps calling `benchmarksStore.getScore(...)` and
`benchmarksStore.getRow(...)`.

The "Benchmark data" hover panel (`cellTips` in `AgentSettingsMatrix.vue`)
calls `formatBenchmarkDetails(cell.benchmark.row, cell.benchmark.scored, taskType)`,
reading `taskType` from the `benchmarkTask` store (today it calls
`formatBenchmarkDetails(cell.benchmark, cell.score)`). The existing `hasData`
test (`cell.score != null && !!cell.benchmark`) is kept. The panel shows:

| Label | Value | Description |
|---|---|---|
| Score | `43` | Our score for this task: 100 = best choice. |
| Intelligence Index / `<type label>` score | `53.6` | The couple's ability on this task type (0–100). |
| Target | `46.0` | The level set by Task difficulty. |
| Cost / task | `$5.12` | Average cost of one task. |
| Time / task | `11.6 min` | Average decode time of one task. |
| Based on | `Intelligence Index; cost and time from Terminal-Bench 4.0` | The Artificial Analysis evaluations used. |

The example values are `claude-opus-5-5` high at the default controls
(`general`, difficulty 50, Favor Cost), with both providers enabled and every
file row available. With `coding` at difficulty 50, the same couple shows
Score `56`, `Coding & terminal score` 58.5, Target `50.5`, `$2.58`, `5.9 min`,
based on `Terminal-Bench 4.0, SciCode`.

- **Cost / task and Time / task** are the task type's values (§4.1: the
  per-type means, or Terminal-Bench 4.0 for `general`). Both rows are shown
  whatever Favor is.
- **"Based on"** joins the `EVAL_LABELS` of the type's evaluations with `', '`. For
  `general` it reads `Intelligence Index; cost and time from Terminal-Bench 4.0`.
- **Formats:**
  - score: integer;
  - ability and target: 1 decimal;
  - cost: `'$' + String(Number(x.toPrecision(3)))` — 3 significant digits,
    trailing zeros dropped: `$5.12`, `$4`, `$0.59`, `$0.0402`, `$0.000175`;
    never `$0.00`;
  - time: round to 1 decimal of a second first (`s = Math.round(x × 10) / 10`);
    if `s < 60`, seconds with 1 decimal (`9.1 s`, `0.3 s`); otherwise minutes
    with 1 decimal (`11.6 min`), so 59.97 s shows `1.0 min`, never `60.0 s`;
  - a `null` value: an em dash.

The empty text (a cell with no score: no row, a row outside the scoring set, or
a non-scorable row such as `claude-opus-5-5` max in Speed mode) becomes:
"Artificial Analysis provides no usable data for this model × effort."

### 5.4 Help page

`frontend/public/help/model-effort-score.md` is rewritten. Keep the title and
the visual-cue list (fill, check mark, dot, solid / dashed ring). New content:

- **Where the data comes from:** Artificial Analysis
  (https://artificialanalysis.ai/), snapshot of 2026-09-23, shipped with TwiCC,
  not refreshed automatically. No mention of how it was collected.
- **The controls:** Task type, Task difficulty, Favor (Cost / Speed),
  Auto-select best, Default provider only.
- **What the score means:** 100 = best couple for this task; 50 = one
  doubling worse; global across enabled providers.
- **Task types:** the table of §4.1 with plain-language descriptions of the
  evaluations.
- **Under the hood:** the target, the curve, the tolerance, no penalty above
  the target, cost / time on a log scale.

## 6. Removals

### 6.1 Backend

| What | Where |
|---|---|
| DeepSWE fetch, extraction, persistence | `src/twicc/benchmarks.py`: delete the file |
| Daily task and broadcast | `src/twicc/benchmarks_task.py`: delete the file |
| Task start / stop | `src/twicc/cli/run.py`: import (L88), `create_task` (L309), cancel (L398-399) |
| DB writer job | `src/twicc/providers/db_writer.py`: `_PersistModelBenchmarksJob`, its dispatch branch, `_apply_persist_model_benchmarks_job` |
| Bootstrap key | `src/twicc/views.py` `bootstrap`: the `benchmarks` query and key, their imports |
| Serializer | `src/twicc/core/serializers.py`: `serialize_benchmark_row` |
| Model | `src/twicc/core/models.py`: `ModelBenchmark` |

Migration: `src/twicc/core/migrations/0144_delete_modelbenchmark.py`, a single
`migrations.DeleteModel(name="ModelBenchmark")`, depending on
`0143_alter_mcpoperation_created_at`. It imports no application code.

### 6.2 Frontend

- `frontend/src/main.js`: remove the `applyBenchmarks(bootstrapData.benchmarks)`
  call and its import if unused.
- `frontend/src/composables/useWebSocket.js`: remove the `benchmarks_updated`
  case.
- `frontend/src/stores/benchmarks.js`: rows come from the JSON import; remove
  `applyBenchmarks`. `getScore(provider, model, effort)` returns the integer
  score or `null`; `getRow(provider, model, effort)` returns
  `{ row, scored }` (the raw row and the §4.6 map value) for a scored couple,
  else `null`; it feeds `formatBenchmarkDetails`. The `scoreLookup` getter calls
  `computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet)`
  with the controls read from `useBenchmarkTaskStore()`; it replaces today's
  read of `useBenchmarkWeightsStore().weightFractions`. The predicate reads
  `useSettingsStore().enabledProviders` and the provider helpers, so scores
  recompute when the controls, the enabled providers or the registries change. The scoring-set predicate (§4.3) is built here
  from the settings store and the provider helpers.
- Delete `frontend/src/stores/benchmarkWeights.js` and
  `AgentSettingsBenchmarkWeights.vue`.

## 7. Tests

No test covers the feature today. Add (`node:test`, auto-discovered by
`cd frontend && npm test`):

- `frontend/src/utils/benchmarkScores.test.js`, on small synthetic rows:
  - normalization of Elo and Omniscience scores;
  - ability, cost and time per type, including the `null` propagation rules;
  - target curve (difficulty 0 → min, 100 → max, 50 → `min + (max − min) × 0.5^0.55`);
  - tolerance scaling by type range;
  - no distance penalty above the target;
  - the best row scores 100, a row with double the metric and no distance
    penalty scores 50;
  - rows rejected by `isInScoringSet` have no score and do not move the range;
  - a non-positive or `null` metric gives no score.
- `frontend/src/data/modelBenchmarks.test.js`: file shape, unique keys, the 10
  evaluation keys on every row, every value a finite number or `null`.
- One regression test on the real file against Appendix B: it calls
  `computeBenchmarkScores(rows, options, () => true)` (every row in the scoring
  set, no Pinia store), orders the result by `penalty` (ascending), and checks the
  target (2 decimals), the top-two couples in that order and their rounded
  scores.
- Degenerate cases (§4.7): empty scoring set, a type range of 0, a General
  range of 0.

The tests read `modelBenchmarks.json` with `readFileSync(new URL(...))` and
`JSON.parse`, like `frontend/src/utils/publicOrigin.test.js`: a static JSON
import without an import attribute fails under `node --test`. The store itself
imports the file statically (`import data from '../data/modelBenchmarks.json'`),
which Vite supports.

## 8. Out of scope

- Any recurring refresh, backend storage or API for the data.
- Exposing scores to the CLI, skills or MCP (nothing exposes them today).
- Remaining quota as a scoring input (possible later).
- A CHANGELOG entry: to propose to the user, not to write unasked.
- The licence question for redistributing Artificial Analysis data: accepted by
  the user for this trial.

## 9. After implementation (user)

Restart the dev servers with `devctl.py`. The `DeleteModel` migration applies at
backend startup.

## Appendix A — data rows

One line per row: `provider|model|effort|intelligence_index|` then the 10
evaluations in this order, separated by `;`, each as `score,cost_usd,time_s`
(empty = `null`):

`terminalbench_4_0; scicode; aa_briefcase; gdpval_aa; gdp_pdf;
automationbench_aa; humanitys_last_exam; critpt; aa_omniscience; aa_lcr`

Values are Artificial Analysis's, rounded to 3 significant digits (the
Intelligence Index to 2 decimals where available).

```
claude_code|claude-fable-5-1|high|51.15|0.52,11.6,1260;0.587,0.0891,11.9;1590,11.4,1120;1620,3.48,433;0.268,2.02,61.9;0.553,1.77,213;0.559,0.397,90;0.303,2.75,602;40.8,0.0207,4.39;0.837,1.48,10.2
claude_code|claude-fable-5-1|low|46.82|0.404,7.32,744;0.567,0.069,7.68;1490,6.72,699;1450,1.43,190;0.28,1.92,39.7;0.522,1.52,176;0.489,0.124,27.5;0.277,1.26,271;34.1,0.00864,1.63;0.823,1.47,7.56
claude_code|claude-fable-5-1|max|53.35|0.52,19.2,1800;0.631,0.659,116;1680,22.7,1760;1730,9.77,990;0.262,2.77,191;0.594,2.61,292;0.591,1.59,300;0.297,5.71,1060;43.5,0.268,50.4;0.853,1.58,28.5
claude_code|claude-fable-5-1|medium|48.92|0.449,9.12,937;0.564,0.0756,9.02;1540,8.58,859;1540,2.18,279;0.268,1.96,47.7;0.547,1.64,194;0.538,0.225,50.8;0.291,1.8,390;37.6,0.0153,3.14;0.847,1.48,8.88
claude_code|claude-fable-5-1|xhigh|53.2|0.551,15.8,1580;0.609,0.241,40.3;1670,17.8,1510;1720,7.22,783;0.262,2.32,115;0.578,2.2,249;0.587,1.03,206;0.311,4.53,890;42.4,0.0773,15.3;0.83,1.51,14.4
claude_code|claude-fable-5|max|49.63|0.424,34.9,1960;0.61,0.268,43.5;1540,22.3,1260;1600,7.75,670;0.24,2.24,98.3;0.541,2.8,303;0.555,0.934,175;0.286,5.62,1020;43.3,0.0698,12.6;0.823,1.57,30
claude_code|claude-opus-4-8|max|41.79|0.217,18.1,2810;0.544,0.266,105;1320,8.26,1340;1440,4.34,918;0.228,1.22,146;0.456,2.48,615;0.487,0.823,348;0.209,1.94,811;28.8,0.0247,10.1;0.777,0.798,41.4
claude_code|claude-opus-5-5|high|53.58|0.566,5.12,696;0.604,0.0402,9.14;1700,6.27,786;1690,1.54,269;0.288,0.825,41.4;0.632,0.699,115;0.556,0.104,35.4;0.309,0.53,177;40.6,0.00853,2.72;0.827,0.592,6.13
claude_code|claude-opus-5-5|low|42.31|0.313,2.08,303;0.586,0.0264,5.61;1280,1.15,214;1220,0.214,54.2;0.256,0.764,23.3;0.529,0.493,95.3;0.483,0.0286,10.7;0.177,0.124,42.4;38.9,0.00577,2.03;0.807,0.587,4.98
claude_code|claude-opus-5-5|max|57.62|0.596,13.1,;0.669,0.469,;1820,21,;1850,8.92,;0.262,1.55,;0.695,1.43,;0.614,0.718,;0.317,2.19,;46.4,0.161,;0.847,0.677,
claude_code|claude-opus-5-5|medium|51.24|0.525,4.04,606;0.593,0.0355,8.83;1640,4.4,668;1580,0.856,187;0.256,0.795,35.7;0.612,0.638,121;0.547,0.0619,24.1;0.277,0.345,130;40.3,0.00755,2.74;0.843,0.59,6.36
claude_code|claude-opus-5-5|xhigh|55.99|0.596,8.78,1140;0.65,0.0676,17.8;1780,12.3,1330;1820,4.21,622;0.266,0.964,86.8;0.65,0.878,147;0.575,0.241,80.3;0.317,1.17,387;42.6,0.0135,4.3;0.847,0.599,8.33
claude_code|claude-opus-5|high|48.12|0.46,13.1,1500;0.554,0.0481,13.5;1570,10.4,1270;1580,3.03,534;0.196,1.03,68.3;0.536,1.07,205;0.528,0.352,157;0.283,1.99,881;33.7,0.0094,3.83;0.79,0.746,12.5
claude_code|claude-opus-5|low|39.35|0.263,5.18,584;0.492,0.0287,5.87;1210,1.78,314;1290,0.582,138;0.172,0.954,32.1;0.518,0.827,146;0.434,0.0625,27.1;0.231,0.72,313;28.6,0.00397,1.4;0.813,0.731,5.82
claude_code|claude-opus-5|max|50.78|0.49,19.3,2270;0.564,0.118,43.3;1670,17.8,1890;1710,6.77,1080;0.216,1.09,90.4;0.566,1.32,268;0.549,0.674,298;0.291,2.88,1270;37.1,0.0259,11.1;0.793,0.761,19.3
claude_code|claude-opus-5|medium|44.83|0.343,9.18,998;0.515,0.0369,8.78;1440,5.25,732;1480,1.36,270;0.2,0.993,48.9;0.543,0.952,173;0.513,0.187,81.8;0.269,1.4,610;31,0.00617,2.35;0.82,0.738,8.9
claude_code|claude-opus-5|xhigh|49.68|0.465,17.4,2120;0.557,0.0723,25.5;1650,14.3,1730;1680,4.96,878;0.21,1.05,78.9;0.532,1.15,247;0.544,0.517,246;0.277,2.46,1160;35.4,0.0143,6.42;0.803,0.753,16.8
claude_code|claude-sonnet-4-6|max|30.06|0.0303,13.3,4330;0.501,0.15,168;1060,2.41,1140;1220,1.94,1040;0.158,0.606,337;0.201,1.18,742;0.336,0.915,1080;0.0314,3.5,4130;12.2,0.044,51.5;0.8,0.385,84.6
claude_code|claude-sonnet-5|high|31.66|0.0505,9.4,1460;0.543,0.0255,16.3;1180,3.76,728;1250,1.02,309;0.094,0.42,59.3;0.321,0.573,235;0.357,0.174,153;0.151,0.799,698;-3.68,0.00196,1.43;0.767,0.302,15.5
claude_code|claude-sonnet-5|low|24.26|0.0253,2.66,589;0.501,0.0132,6.22;921,0.817,279;1060,0.26,122;0.094,0.367,26.4;0.199,0.305,122;0.219,0.0256,23.9;0.0457,0.232,216;-8.23,0.000658,0.305;0.673,0.292,4.52
claude_code|claude-sonnet-5|max|38.16|0.141,19.7,2410;0.543,0.192,135;1360,14.4,1530;1450,4.7,880;0.132,0.615,192;0.365,1,415;0.413,1.02,746;0.169,2.31,1690;16.4,0.029,21;0.82,0.368,59.2
claude_code|claude-sonnet-5|medium|28.05|0.0202,5.53,1050;0.516,0.0171,9.6;1050,1.73,438;1140,0.529,201;0.116,0.395,39.4;0.279,0.431,174;0.3,0.0847,78.5;0.0857,0.469,430;-6.87,0.00104,0.656;0.737,0.296,7.92
claude_code|claude-sonnet-5|xhigh|34.38|0.0707,12.8,1860;0.541,0.0399,28.9;1280,7.56,1200;1340,2.14,567;0.126,0.445,81.4;0.345,0.715,312;0.39,0.272,239;0.154,1.16,1020;3.18,0.00428,3.48;0.767,0.316,25.5
codex|gpt-5.5|high|36.98|0.0909,5.59,426;0.561,0.107,35.6;1090,3.3,319;1310,1.41,193;0.226,0.926,75.8;0.443,1.81,154;0.45,0.227,85.1;0.254,0.92,344;18.8,0.0859,32;0.843,0.508,13.6
codex|gpt-5.5|medium|33.8|0.0505,2.99,267;0.545,0.0622,20.2;1000,1.92,222;1220,0.808,134;0.206,0.884,46.3;0.401,1.33,122;0.424,0.126,50.8;0.186,0.294,112;18.1,0.0373,15;0.83,0.493,8.77
codex|gpt-5.5|xhigh|38.36|0.146,11.6,599;0.558,0.168,52.8;1140,5.15,385;1340,2.19,238;0.212,1.06,96.4;0.473,2.33,178;0.458,0.38,129;0.271,1.61,544;20.5,0.152,51.1;0.843,0.52,16.4
codex|gpt-5.6-luna|high|32.12|0.0253,0.106,249;0.516,0.00293,15.6;1170,0.117,241;1320,0.045,132;0.222,0.0404,51.6;0.356,0.0562,110;0.334,0.00951,60.7;0.166,0.019,118;-12,0.0025,15.9;0.803,0.0196,4.47
codex|gpt-5.6-luna|low|21.01|0,0.0118,40.7;0.461,0.00147,6.25;738,0.0125,34.9;981,0.00816,32;0.142,0.0341,11;0.117,0.0171,36;0.198,0.00144,8.86;0.0257,0.00367,19.6;-14.7,0.000491,3;0.7,0.0191,1.82
codex|gpt-5.6-luna|max|37.32|0.116,0.849,955;0.536,0.00927,51.8;1350,0.395,556;1440,0.11,243;0.24,0.0568,146;0.502,0.0912,165;0.395,0.0293,174;0.206,0.0633,352;-10.3,0.011,65.1;0.837,0.0209,12.2
codex|gpt-5.6-luna|medium|25.04|0.00505,0.0251,66.2;0.468,0.0018,8.71;932,0.0293,75.6;1110,0.0148,55.6;0.152,0.0353,17.8;0.231,0.0291,61.6;0.258,0.0029,18.9;0.0486,0.00659,39.6;-13.2,0.000817,5.3;0.75,0.0193,2.57
codex|gpt-5.6-luna|xhigh|34.56|0.0354,0.3,465;0.505,0.00447,26;1270,0.213,387;1380,0.0723,194;0.238,0.0465,102;0.426,0.0721,141;0.37,0.0167,109;0.206,0.0395,251;-10.8,0.00502,32.6;0.817,0.0199,6.87
codex|gpt-5.6-sol|high|42.35|0.207,2.22,458;0.578,0.0353,17.6;1370,2.06,439;1480,1.11,290;0.278,0.7,67.4;0.553,0.741,170;0.46,0.0828,55.6;0.257,0.245,158;20.4,0.018,12;0.817,0.386,5.86
codex|gpt-5.6-sol|low|33.47|0.0101,0.524,149;0.564,0.0241,11.5;1040,0.419,132;1290,0.268,115;0.21,0.63,23.1;0.41,0.47,126;0.394,0.0224,16.9;0.149,0.0751,49.6;18.9,0.00589,4.31;0.78,0.382,3.47
codex|gpt-5.6-sol|max|46.97|0.399,8.09,926;0.571,0.0797,42.3;1490,4.02,698;1590,2.81,488;0.272,0.929,170;0.601,0.972,201;0.495,0.268,161;0.323,0.859,508;22,0.0854,51;0.84,0.402,14.6
codex|gpt-5.6-sol|medium|39.24|0.146,1.64,361;0.574,0.0285,14.9;1240,0.968,269;1400,0.595,211;0.262,0.653,40.9;0.513,0.601,168;0.422,0.043,33.1;0.229,0.126,89.1;19.4,0.0102,7.74;0.803,0.383,4.52
codex|gpt-5.6-sol|xhigh|44.01|0.247,3.46,675;0.571,0.0449,24.2;1440,3.08,623;1550,1.7,409;0.276,0.782,119;0.553,0.811,191;0.473,0.142,95.9;0.286,0.437,285;21,0.0331,22.2;0.823,0.388,7.97
codex|gpt-5.6-terra|high|34.24|0.0152,0.628,283;0.524,0.0232,19.3;1200,0.861,317;1360,0.532,266;0.208,0.354,50.7;0.42,0.404,137;0.385,0.0619,64.1;0.229,0.174,175;-3.47,0.00975,9.95;0.777,0.193,4.4
codex|gpt-5.6-terra|low|27.5|0.0152,0.243,118;0.499,0.0119,8.12;1010,0.3,134;1100,0.102,68.7;0.176,0.346,25.6;0.291,0.269,92.1;0.292,0.0142,15.5;0.0943,0.0434,42;-6.83,0.00285,2.97;0.713,0.191,2.34
codex|gpt-5.6-terra|max|42.08|0.354,6.39,1570;0.55,0.114,109;1340,2.83,775;1430,1.24,488;0.24,0.589,265;0.596,0.73,253;0.429,0.228,226;0.3,0.608,574;0.05,0.0703,69.6;0.83,0.213,24.1
codex|gpt-5.6-terra|medium|30.09|0.0101,0.341,161;0.505,0.015,11;1040,0.384,156;1250,0.204,121;0.17,0.349,27.6;0.335,0.301,102;0.333,0.0255,26.8;0.174,0.0696,67.5;-5.13,0.00466,4.76;0.74,0.191,2.91
codex|gpt-5.6-terra|xhigh|37.95|0.101,1.57,593;0.523,0.0325,28.1;1340,1.81,588;1430,0.804,363;0.246,0.416,111;0.471,0.528,175;0.419,0.0957,96.2;0.271,0.291,286;-2.98,0.0162,16.1;0.79,0.195,6.23
codex|gpt-6-astra|high|50.92|0.54,4.05,608;0.554,0.0696,18.8;1510,4.76,642;1480,2.43,373;0.31,1.79,64.4;0.666,1.3,184;0.531,0.162,63.4;0.289,0.434,159;43.7,0.0272,10.4;0.8,0.96,6.55
codex|gpt-6-astra|low|45.78|0.419,2.25,314;0.541,0.0425,8.54;1260,1.44,209;1370,0.855,141;0.304,1.7,26.4;0.591,0.999,128;0.492,0.0389,15.1;0.263,0.145,47.2;40.5,0.00696,2.42;0.8,0.95,2.86
codex|gpt-6-astra|max|52.67|0.591,8.5,1290;0.565,0.232,71.5;1570,9.5,1170;1540,4.53,683;0.31,2.08,158;0.685,1.6,220;0.547,0.378,128;0.317,1.17,386;43.4,0.097,32.7;0.807,0.997,18.4
codex|gpt-6-astra|medium|49.57|0.495,4.43,612;0.542,0.0509,11.6;1460,3.94,545;1470,1.82,267;0.304,1.72,35.5;0.646,1.18,161;0.527,0.101,40.2;0.291,0.285,103;42.2,0.015,5.69;0.797,0.953,4.09
codex|gpt-6-astra|xhigh|52.39|0.596,5.86,803;0.557,0.122,36.6;1540,6.56,830;1520,3.04,446;0.322,1.91,104;0.672,1.41,191;0.546,0.253,92.8;0.314,0.798,283;43.4,0.0502,18.2;0.8,0.97,9.87
codex|gpt-6-luna|high|32.1|0.0455,0.12,512;0.503,0.00098,10.5;1180,0.0592,293;1290,0.0321,175;0.138,0.0189,38.7;0.478,0.0207,115;0.329,0.00304,41.5;0.154,0.00576,75.2;-5.5,0.000583,7.87;0.793,0.0097,3.73
codex|gpt-6-luna|low|20.9|0,0.00545,24.5;0.469,0.000511,3.91;725,0.00583,27.4;992,0.00384,27;0.064,0.0166,6.31;0.12,0.00588,21.5;0.203,0.000522,6.51;0.0257,0.00118,12.2;-9.17,0.000175,2.17;0.74,0.00956,1.7
codex|gpt-6-luna|max|37.3|0.126,0.236,861;0.546,0.00368,42.2;1300,0.171,685;1370,0.0904,385;0.204,0.0285,152;0.532,0.0364,208;0.385,0.0118,144;0.194,0.0266,320;0.65,0.00252,30.7;0.833,0.0102,9.21
codex|gpt-6-luna|medium|29.5|0.0253,0.0716,319;0.509,0.000699,6.86;1060,0.0311,152;1220,0.0177,106;0.14,0.0175,19.1;0.405,0.0166,87.8;0.283,0.00162,22.4;0.106,0.00318,41;-5.05,0.000367,5.02;0.783,0.00964,2.86
codex|gpt-6-luna|xhigh|33.88|0.0808,0.198,701;0.517,0.00144,16.4;1220,0.08,372;1300,0.0448,219;0.168,0.0206,59.6;0.478,0.0233,135;0.343,0.00509,68.1;0.174,0.009,117;-1.83,0.000963,12.8;0.8,0.00982,5.17
codex|gpt-6-sol|high|42.8|0.263,1.6,344;0.549,0.013,8.11;1290,0.634,161;1380,0.483,142;0.28,0.348,22.7;0.601,0.254,77.8;0.441,0.0331,28.8;0.254,0.0866,71.9;26.8,0.00635,5.41;0.837,0.191,2.38
codex|gpt-6-sol|low|33.9|0.0909,0.489,137;0.502,0.00743,2.87;905,0.125,34;1180,0.0869,33.4;0.218,0.331,6.76;0.539,0.191,51.4;0.349,0.00698,5.25;0.163,0.0219,13.8;26.5,0.00156,1.08;0.793,0.19,1.1
codex|gpt-6-sol|max|47.5|0.439,4,750;0.576,0.0425,29.7;1480,2.67,534;1490,1.32,320;0.248,0.429,81.7;0.616,0.358,104;0.479,0.12,91.7;0.309,0.324,245;27.1,0.0269,20.5;0.837,0.198,7.34
codex|gpt-6-sol|medium|39.8|0.187,1.12,264;0.538,0.00949,5.18;1140,0.337,93.7;1320,0.238,83.6;0.254,0.337,13.7;0.58,0.218,69;0.41,0.0175,15.6;0.246,0.0461,37.8;27,0.00335,2.87;0.823,0.191,1.93
codex|gpt-6-sol|xhigh|44.1|0.303,1.91,417;0.551,0.0215,14.3;1360,1.19,270;1440,0.767,205;0.238,0.372,40.3;0.617,0.292,85.2;0.463,0.0591,47.8;0.28,0.154,121;26.7,0.0117,9.4;0.813,0.193,3.76
```

## Appendix B — reference results

Computed by an independent implementation of §4 over the 56 rows of
Appendix A, with both providers enabled and every row in the scoring set.
Top two couples per case, **ranked by unrounded penalty** (lowest first), with
their rounded score:

| Task type | Favor | Difficulty | Target | Top two |
|---|---|---|---|---|
| `general` | Cost | 0 | 20.90 | `codex gpt-6-luna low` 100 · `codex gpt-5.6-luna low` 46 |
| `general` | Cost | 50 | 45.98 | `codex gpt-6-sol xhigh` 100 · `codex gpt-6-astra low` 99 |
| `general` | Cost | 100 | 57.62 | `claude_code claude-opus-5-5 xhigh` 100 · `claude_code claude-opus-5-5 high` 95 |
| `general` | Speed | 50 | 45.98 | `codex gpt-6-astra low` 100 · `codex gpt-6-sol xhigh` 65 |
| `coding` | Cost | 50 | 50.51 | `codex gpt-6-astra low` 100 · `codex gpt-6-sol max` 71 |
| `office` | Cost | 100 | 53.23 | `claude_code claude-opus-5-5 high` 100 · `claude_code claude-opus-5-5 xhigh` 82 |
| `knowledge` | Speed | 0 | 42.65 | `claude_code claude-sonnet-5 low` 100 · `claude_code claude-sonnet-5 medium` 46 |
| `longdocs` | Cost | 100 | 85.30 | `codex gpt-6-luna max` 100 · `codex gpt-5.6-luna max` 63 |

With `coding` / Cost / 50, `claude_code claude-opus-5-5 medium` also rounds to
71 but ranks third (unrounded 70.60 against 71.18 for `codex gpt-6-sol max`).

With `general` / Speed at any difficulty, `claude_code claude-opus-5-5 max`
has no score (its `time_s` is `null`).
