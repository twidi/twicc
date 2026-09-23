---
title: "Model × effort scores"
---

The model picker is a **matrix**: one row per model, one column per
reasoning effort. Each selectable cell shows a **score from 0 to 100**:
how well that model, at that effort, fits the task you describe with the
controls below the matrix. Higher is better. A quiet **?** means there is
no usable data for that pair.

The matrix carries a few visual cues:

- the cell's **background** gets more colored as the score rises;
- a **check mark** marks the currently selected cell, a small **dot** the
  default one;
- a **ring** highlights each provider's best score — solid for your
  default provider, dashed for the others when several providers are
  shown.

## Where the data comes from

Scores are built from the evaluations published by
**[Artificial Analysis](https://artificialanalysis.ai/)**, an independent
benchmarking site. It measures each model, at each reasoning effort, on
many evaluations, with the **cost** and **time** of each task.

TwiCC ships a snapshot of these results taken on **2026-09-23**. It is not
refreshed automatically.

## The controls

- **Task type** — what kind of work you are about to do (see below).
  *General* uses Artificial Analysis's overall Intelligence Index.
- **Task difficulty** — how capable the model must be. Slide it up for a
  hard task, down for an easy one.
- **Favor** — **Cost** to prefer cheaper pairs, **Speed** to prefer faster
  ones.
- **Auto-select best** — selects the best-scoring cell for you whenever a
  control changes, exactly as if you clicked it. When several providers
  are shown, **Default provider only** restricts that pick to your default
  provider.

## What the score means

The difficulty sets a **target level** on the task type's ability scale.
A pair **at or above** the target does the job: among those, the cheaper
(or faster) one wins. A pair slightly **below** the target loses a few
points; a pair far below it loses so many that a low price cannot save it.

- **100** is the best pair for this task.
- **50** is "one doubling worse": twice the cost (or time) of the best,
  or far enough below the target to count as much.
- Pairs further away keep **small, non-zero** scores. Several of them
  may show the same number: the **ring** still marks each provider's
  best pair, so you can pick the best one of a single provider.
- Scores compare **every enabled provider** together, so you can choose
  across Claude Code and Codex. Disabled providers, retired or disabled
  models, and efforts a model does not support never count.

## Task types

| Task type | Based on |
|---|---|
| General | Intelligence Index (Artificial Analysis's overall score); cost and time from Terminal-Bench 4.0 |
| Coding & terminal | Terminal-Bench 4.0 (agentic coding in a terminal), SciCode (scientific coding) |
| Office & knowledge work | AA-Briefcase (knowledge-work deliverables), GDPval-AA (real-world work tasks), GDP.pdf (document reasoning) |
| Automation & tools | AutomationBench-AA (SaaS workflows with tools) |
| Science & reasoning | Humanity's Last Exam (hard reasoning), CritPt (physics), SciCode |
| Factual knowledge | AA-Omniscience (knowledge and hallucination) |
| Long documents | AA-LCR (long-context reasoning) |

For a type built on several evaluations, the ability is their average
(each on a 0–100 scale), and so are the cost and the time.

## Under the hood (for the curious)

- The slider maps **0** to the weakest pair and **100** to the strongest
  one for the chosen task type, on a curve that climbs fast at first: the
  middle of the slider already asks for a capable model.
- **No penalty above the target**: a pair that exceeds it only pays for
  its cost (or time).
- Being **2 Intelligence Index points** below the target weighs as much as
  paying twice as much; other task types scale this by their own range.
  The penalty grows with the **4th power** of the gap: 4 points below
  weighs as much as paying 2¹⁶ times more.
- The score falls from 100 to 50 over the first doubling, then more and
  more slowly, so it never collapses to 0.
- Cost and time are compared on a **logarithmic** scale: what matters is
  the ratio (from $1 to $2 counts like $10 to $20).
- Time is Artificial Analysis's time per task: the time spent generating
  the answer, without start-up delays.
