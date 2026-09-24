# Exhaustive review of a pull request

Patterns: `multi-angle` + `produce-refute`, then synthesize.
Shape: leader → N read-only analysts (one per dimension); the leader verifies the
serious findings with a second pass, then synthesizes.

## Walkthrough
1. Split the review into dimensions: correctness, security, performance, tests.
2. Per dimension, spawn a read-only analyst on the repo (safe — it can't write):
   `create-session --hidden --permission-mode dontAsk --annotation job=security
    'Review the diff for security issues only; list each as file:line + severity.'`
   Read-only (safe — it can't touch the code); you will pull it.
3. Barrier: `sessions wait-reply <ANALYST_ID>... --wait-timeout 300`,
   repeated for the ids still on `timeout`.
4. Pull each analyst (`session <id> messages --tail 1`); collect all findings.
5. For each HIGH finding, a quick produce-refute: spawn one executor to confirm it is
   real and reproducible; drop the refuted ones.
6. Synthesize: group by file, rank by severity, write the review.

## Notes
- Analysts are read-only on purpose — a review must not touch the code.
- Fast pass: skip step 5. Higher assurance: add a Codex analyst beside Claude (cross-provider).
