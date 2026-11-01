# WebSocket responsiveness design review

Date: 2026-09-29
Design: [WebSocket responsiveness and indexed session history](2026-09-29-websocket-sync-responsiveness-design.md)
Source baseline: `fb51f74e`.
Outcome: **PASS after corrections.** No remaining blocking or major finding in the reviewed design.

## Scope and method

The user authorized renewed analysis, a specification, and independent review.
Two analysis agents examined compute integration and WebSocket isolation before the specification was written.
A fresh reviewer then checked the complete design against the source.
The compute analyst separately reviewed sections 6–7 and rechecked the corrections.

This is a design review, not proof that an implementation passes tests.
No product code, database contents, dependencies, or running services changed during this design stage.
The source investigation and dependency-only executor experiment inform the design's claims and limits.

## Reviewers

| Reviewer | Scope | Final outcome |
| --- | --- | --- |
| `/root/spec_review` | Fresh review of transport, authentication, lifecycle, executor, compute publication, and live scheduling | PASS |
| `/root/compute_analysis` | Fact schema, extraction, current-version authority, stale fallback, rollout replacement, and live transaction state | PASS after corrections |

## Findings and dispositions

### R1 — Major: one-shot enrichment and speculative suffix loss

The original draft allowed cache invalidation after rollback and a time deadline between processed lines.
Live ingestion transforms all selected records before its second pass links and persists metadata.
That first pass consumes original-file enrichment unavailable in the source JSONL.

Source references:

- `src/twicc/providers/compute_base.py:3995`: bulk insertion followed by the chronological second pass.
- `src/twicc/providers/codex/compute.py:5404`: `pop_original_files` consumption.
- `src/twicc/providers/claude_code/compute.py:2587`: `pop_original_file` consumption.

Discarding prepared suffix records or invalidating their caches can permanently lose full diff content.

**Correction:** select the raw slice before transformation and commit every selected record through both passes.
Treat the time target as a tuning objective for future slices, not a cutoff inside a prepared transaction.
Borrow one-shot enrichment until commit, or restore consumed entries on rollback.
Preserve concurrent cache additions and replacements.
Add failure-after-enrichment and second-pass time-target regression cases.

**Recheck:** compute reviewer confirms the revised contract preserves enrichment and prepared state.

### R2 — Contract contradiction: cancellation and publication

The original draft said cancellation cannot publish a complete index.
The existing writer deliberately shields admitted transactions until their actual completion.
A cancelled caller can therefore complete a valid atomic commit.

**Correction:** missing, superseded, and rolled-back application cannot publish current facts.
Cancellation drains admitted work; successful atomic publication remains permitted.
Add an explicit cancellation-during-final-apply regression case.

**Recheck:** compute reviewer confirms consistency with the writer's lock and drain contract.

### R3 — Transport clarification: suppressed send failures

`WSConsumer.send_json` catches all send exceptions at `src/twicc/asgi.py:938`.
The heartbeat adapter must observe failures to terminate correctly.

**Correction:** encode pong through the existing encoder, then use the adapter-owned raw send path.
Observe all raw send failures below callers that can suppress exceptions.
Preserve existing pong whitespace so the frontend visibility probe continues to observe it.

**Recheck:** fresh reviewer confirms the corrected transport design has an explicit failure path.

## Confirmed design requirements

- Historical facts use the normal provider compute-version rebuild, with no separate backfill subsystem.
- The existing revision guard precedes atomic facts, relations, metadata, and version publication.
- Outdated sessions cannot trust partial indexed matches.
- Raw rollout replacement invalidates facts inside the replacement transaction.
- Heartbeat bypasses suspended business handlers only after successful authentication and accept.
- Normal commands and snapshots remain sequential.
- Transport queues are bounded; network and queue saturation remain explicit limits.
- Live slices release both relevant locks and requeue ready backlog behind other files.
- `process_path` completion keeps its existing drained-path meaning.
- Existing cancellation shielding and intentionally detached agent operations remain intact.

## Residual limits and implementation checks

Outdated-session fallback can traverse the full transcript until normal compute succeeds.
The final compute application retains its outer writer lease and can delay business writes.
One large source record can exceed live slice time and byte targets.
Network backpressure, FIFO saturation, or unrelated event-loop blocking can still delay heartbeat.

Implementation must execute the deterministic and workload tests in design section 9.
The read-only measurements establish plausible mechanisms, not attribution of every observed outage.

Documentation checks: no unresolved placeholders; whitespace and staged-scope checks before commit.
Product tests are not run for these documentation-only changes.
