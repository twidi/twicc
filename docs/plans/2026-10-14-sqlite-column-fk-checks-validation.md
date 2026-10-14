# SQLite column FK checks validation

Actual migration replay from historical 0157 performs **zero FK row checks** with the dedicated backend.
The standard backend performs one global check for each of 0158, 0159, and 0160.
Replay tests verify retained Session and SessionItem rows, rebuilt queue IDs and values, nullable columns, JSON checks, and restored FK enforcement.
The 0160 test also verifies the actual rebuild INSERT and existing-row JSON defaults.

## Benchmark

Django 6.1.2; SQLite 3.53.1. Values are medians of three runs per backend and size.
Each backend starts from a byte-identical historical schema and populated database copy.
Both use `PRAGMA synchronous=OFF`; database construction, copying, and queue population are outside migration timing.
The unrelated child table contains 100,000 or 1,000,000 valid FK rows referencing one parent.
The historical application tables contain one session and ten items; 0160 copies 10,000 or 100,000 queue rows.
Initial database sizes are 1,548,288 and 9,691,136 bytes respectively.

| Unrelated FK rows | Queue rows at check | Migration | Backend | Checks / scope | Total (s) | FK check (s) | Rebuild INSERT (s) | Final bytes |
| ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 100,000 | 0 | 0158 | dedicated | 0 / none | 0.145475 | 0.000000 | 0.000000 | 1,552,384 |
| 100,000 | 0 | 0158 | standard | 1 / global | 0.077981 | 0.006262 | 0.000000 | 1,552,384 |
| 100,000 | 10,000 | 0159 | dedicated | 0 / none | 0.038575 | 0.000000 | 0.000000 | 1,732,608 |
| 100,000 | 10,000 | 0159 | standard | 1 / global | 0.007413 | 0.005607 | 0.000000 | 1,732,608 |
| 100,000 | 10,000 | 0160 | dedicated | 0 / none | 0.117823 | 0.000000 | 0.003721 | 1,941,504 |
| 100,000 | 10,000 | 0160 | standard | 1 / global | 0.015979 | 0.005521 | 0.003575 | 1,941,504 |
| 1,000,000 | 0 | 0158 | dedicated | 0 / none | 0.164002 | 0.000000 | 0.000000 | 9,695,232 |
| 1,000,000 | 0 | 0158 | standard | 1 / global | 0.123440 | 0.055616 | 0.000000 | 9,695,232 |
| 1,000,000 | 100,000 | 0159 | dedicated | 0 / none | 0.042234 | 0.000000 | 0.000000 | 11,685,888 |
| 1,000,000 | 100,000 | 0159 | standard | 1 / global | 0.060280 | 0.058424 | 0.000000 | 11,685,888 |
| 1,000,000 | 100,000 | 0160 | dedicated | 0 / none | 0.162981 | 0.000000 | 0.041164 | 13,979,648 |
| 1,000,000 | 100,000 | 0160 | standard | 1 / global | 0.123587 | 0.065651 | 0.044318 | 13,979,648 |

Total time includes migration application, historical model-state updates, and recorder writes.
FK time measures `check_constraints()` separately; trace callbacks confirm every check scope.
Rebuild INSERT time measures row copying only; it excludes table creation, drop, rename, and commit.
The dedicated backend eliminates scans but retains metadata-proof overhead and rebuild copying.
These small synthetic databases do not establish a universal total-time speedup or production timings.
DevCTL background compute remains active elsewhere and can affect timings; no server action occurs for this validation.
All final databases retain valid FK state and expected row counts; temporary databases are deleted.

## Checks

Migration-focused suite: **475 passed, 5 skipped**, 82.36 seconds.
Final replay assertions: **2 passed**, 12.76 seconds.
Ruff and `git diff --check` pass.
The full suite runs before the final column-signature assertions; the replay rerun verifies those assertions.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/*migration*.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_sqlite_migration_integration.py -k actual_158 -q
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && uvx ruff check tests/test_sqlite_migration_integration.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && git diff --check
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD PYTHONPATH=$PWD uv run python .superpowers/sdd/2026-10-14-sqlite-column-fk-checks-implementation/task2-benchmark.py
```
