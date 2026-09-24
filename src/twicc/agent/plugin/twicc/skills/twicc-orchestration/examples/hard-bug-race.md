# Crack a hard bug by racing approaches

Patterns: `speculative-race` (with diverse approaches).
Shape: leader → K parallel attempts on the same bug → first acceptable fix wins.

## Walkthrough
1. Spawn K executors on the same bug, each told to try a *different* angle
   (`--annotation attempt=<k>`): e.g. add logging, bisect, rewrite the suspect function,
   check the dependency. Vary model/provider too.
2. First-wins: `sessions wait-reply <ATTEMPT_ID>... --wait-first --wait-timeout 300`.
   Only `outcome == "replied"` is a finisher; an attempt on `awaiting_user_input`
   needs an answer, or a new wait without it.
3. Inspect the finisher: does its fix actually pass the repro/tests? If yes,
   stop the other attempts by explicit ids, or mark them `status=loser` and run
   `sessions stop --spawned-by self --annotation status=loser --timeout <N>`.
   If not, wait again, naming only the other ids. Re-running resumes, except for
   a session whose compute is not current; `--since <the instant the batch
   started>` resumes in every case.
4. Use the winning fix.

## Notes
- "First done" is not "correct" — validate the finisher (run the repro) before stopping the rest.
- Always stop the losers, or they keep burning cost.
- Diversity is the whole point — identical attempts don't improve the odds.
