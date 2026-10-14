# SQLite column operations and foreign-key checks

## Problem

The dedicated migration backend performs global foreign-key checks for ordinary column additions and existing-table rebuilds. Migration 0158 spends 114.29 seconds checking foreign keys. Migration 0160 spends 105.45 seconds checking foreign keys. Neither operation changes application FK values.

## Required behavior

Decide from the physical schema and executed operations, without a Django field-type allowlist.

| Operation | Required decision |
| --- | --- |
| Add an ordinary column while preserving relation keys | No FK row check |
| Add a nullable FK or OneToOne column whose existing values are NULL | No FK row check; validate the parent key definition |
| Add an FK with populated existing values | Check affected child tables |
| Remove an unrelated column or an outbound FK while preserving remaining references | No FK row check |
| Rebuild an existing populated table with unchanged reference values and key semantics | No FK row check |
| Change a referenced key or FK values with known dependencies | Check affected child tables |
| Execute unclassified schema changes | Preserve the global fallback |

Inbound references matter even when the changed field is not a relation. Referenced keys must retain their type, collation, uniqueness, and column identity. A proof cannot assume model state matches the physical schema.

## Operation boundaries

Cover direct standard AddField and RemoveField operations. Cover Django rebuilds for adding or removing a field and unchanged mechanical rebuilds. Arbitrary AlterField transformations retain the existing conservative behavior.

Recognize the actual SQL and mechanical SQLite authorization events. Verify identity copying of retained columns during rebuilds. Compare retained declarations and constraints when relations depend on them. A table without inbound or outbound relations needs no relation-preservation proof beyond identifying its mechanical rebuild effects.

Suppress only effects belonging to the verified operation. Keep independent callback writes, trigger effects, unknown schema changes, and data migrations. Translate residual temporary-table writes to their final table when their identity is known. Temporary rebuild tables are not genuinely new logical parent tables.

Do not read all application rows to prove an unchanged operation. Preserve transaction ownership, rollback, observer cleanup, and restored FK enforcement.

## Acceptance

Apply the actual migrations 0158 and 0160 to a disposable database. Both perform zero FK row checks. Record total migration time separately from FK-check time.

Test populated related tables, nullable OneToOne fields, ordinary fields with defaults and checks, column removals, invalid new parent keys, changed copy mappings, schema drift, callbacks, and rollback. Retain checks for genuine invalid FK writes and incoming references to newly created parent tables.

Use disposable databases for timing. Do not modify the running worktree database or the main database. A SQLite table rebuild can still copy rows; eliminating FK checks does not eliminate that copy.
