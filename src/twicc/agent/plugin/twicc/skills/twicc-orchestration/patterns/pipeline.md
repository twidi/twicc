# Pipeline

Run stages in sequence: each stage's output is the next stage's input.

Shape: chain · files (+ short messages) · staged · chain (no merge) · often heterogeneous.

## Who does what
- **Leader or manager** — own the chain. Either spawn stage 1, wait, feed its output
  to stage 2, and so on; or spawn the stages and pass each the previous stage's
  scratch file. You hold the baton between stages.
- **Workers (or sub-managers)** — each performs one stage: reads the previous
  stage's artifact from the shared scratch, writes its own, reports done.

## Protocol
1. Define the stages and the artifact handed between them.
2. Run stage K: brief it with the location of stage K-1's output in the shared
   scratch (`scratch_dir`) and where to write its own.
3. Wait for stage K (`create-session --wait-reply`, or
   `sessions wait-reply <STAGE_K_ID>`), confirm its artifact,
   then start stage K+1.
4. The last stage's artifact is the result.

## Variant: peer handoff (no central baton)
Instead of relaying between stages yourself, spawn all stages up front and let each
one hand **directly** to the next: when stage K finishes it messages stage K+1
(`send-message <next_stage_id> 'done — input at <scratch_dir>/<id>-out.md'`) rather
than reporting to you for relay. You step back to supervising and only wait on the
final stage. This trades your turn-by-turn control of the cadence for lower latency
(no parent round-trip between stages) — worth it for long chains where each handoff
is just "next, here's the artifact". Brief each stage with its successor's id (or
have it discover the successor via `sessions --siblings self`). See
`patterns/peer-coordination.md`.

## Use it when
Work is inherently sequential — transform, then validate, then format.
Not when stages are independent (→ scatter-gather).

## Pitfalls
- A broken middle stage stalls the chain — check each artifact before the next
  stage; re-run a stage in place if needed (idempotent).
- Pass artifacts through the **scratch**, not through giant messages.
- A read-only worker can be a stage only if it merely reads/analyzes and emits text;
  it can't write the next artifact.

Examples: `examples/content-pipeline.md`.
