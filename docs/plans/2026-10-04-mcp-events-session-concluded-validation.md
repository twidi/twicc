# MCP Events acceptance validation

Authority: `2026-10-03-mcp-events-session-concluded-spec.md`, revision 23, §§11–14.
Date: 2026-10-04. Worktree: `/home/twidi/dev/twicc-poc/.worktrees/mcp-events`.
Automated acceptance does not approve release. Task 15 remains a mandatory manual gate.

## Environment and scope

- Python 3.13.14; Django 6.0.4; pytest 9.0.3.
- Installed packages: **mcp 2.1.1**, **standardwebhooks 1.1.0**.
- `twicc.__file__` resolves inside this worktree's `src/twicc/`.
- Every Python command uses `TWICC_DATA_DIR=$PWD` from the worktree.
- Tests use `twicc.settings_test` and the test database. No running-instance migration or restart occurs.
- The inherited `VIRTUAL_ENV` mismatch warning is expected. No command uses `--active` or `uv pip`.
- No plugin version, plugin skill, changelog, frontend, CLI command, RPC route, or MCP tool change occurs.
- One migration, `0151_mcp_event_subscriptions.py`, adds the subscription model and `Session.history_epoch`.
- `_SessionWait` remains unchanged. Event registration applies only to `_external_server`.
- The only dependency addition is the previously authorized `standardwebhooks>=1.1.0`.

## Rebase validation — 2026-10-05

The feature is not present in local `main` before this rebase.
All 18 feature commits replay onto `main` at `92e0633cf88144fe2576fbf44d099cee8d0bd62c` without textual conflicts.
The backup ref is `backup/mcp-events-before-main-rebase-2026-10-05`.

Both branches originally use migration number 0150. The event migration becomes
`0151_mcp_event_subscriptions`, with `0150_session_automatic_titles` as its dependency.
The migration regression checks the single core leaf, the dependency, existing title state,
and the zero history epoch. The worktree database has no migration history to reconcile.
No running-instance migration or server restart occurs.

The rebase also duplicates the explicit provider-settings fixture in `test_wait_reply.py`.
The duplicate is removed; the main fixture remains. Main's `--from-line` CLI spelling,
automatic-title state, title hooks, and provider helper changes remain present.

Fresh checks after the rebase:

| Command | Result |
|---|---|
| `TWICC_DATA_DIR=$PWD uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test` | No changes detected. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp*.py tests/test_title*.py tests/test_wait_reply.py tests/test_wait_background.py tests/test_wait_default_cursor.py tests/test_cli_session_wait.py tests/test_cli_sessions_wait_reply.py tests/test_send_messages_wait.py tests/test_codex_rollout_migration.py tests/test_codex_hardcoded_commands.py tests/test_history_facts.py tests/test_codex_recompute_persistence.py tests/test_watcher_source_identity.py tests/test_watcher_catch_up.py tests/test_live_sync_slices.py tests/test_sqlite_migration_integration.py --tb=short` | **1498 passed**. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_storage.py tests/test_title_state.py tests/test_sqlite_migration_integration.py --tb=short` | **28 passed**, including the strengthened migration regression. |
| `uvx ruff check <existing Python paths changed from main>` | All checks passed. |
| `git diff --check` | Passed. |
| `git merge-base --is-ancestor main HEAD` | Passed. |

Task 15 remains pending. This rebase does not approve the manual release gate.

## Acceptance audit findings

The audit closes five deferred review items:

1. Compare the complete catalog and nested schemas against the spec's JSON, including descriptions.
2. Read streamed responses across multiple chunks; stop on overflow and close the response.
3. Cancel all verification callers; collect the shared task without awaiting its exception.
4. Combine invalid schema, secret, URL, and lookup inputs to verify validation order.
5. Accept both 24-byte and 64-byte secrets, with and without padding.

The cumulative lint check finds a watcher regression from command-helper extraction.
The extraction removes two imports still required by SYSTEM interruption parsing.
The new watcher test first fails for interruption and passes for normal prompts and commands.
Restoring those imports makes all three actual bridge branches pass.

The first cumulative events run reports **478 passed, 14 failed**.
Seven failures concern disabled loggers after a CLI test calls `django.setup()`.
The test settings disable existing loggers. Scoped fixtures enable only the loggers whose messages tests assert.

Seven failures concern OAuth fixture import order.
A delivery fixture imports OAuth consumers while `config.base_url` is patched.
Those consumers retain the patched function after teardown.
Importing consumers before patching restores test isolation. No OAuth production behavior changes.

The first new focused run also exposes two incorrect test assumptions and one test event-loop lifetime error.
The schema reason contains the JSON-schema error, not its property path.
A cancelled caller retains the leader through its traceback until the caller is released.
A writer queue must stay on one event loop. The tests now respect these contracts.

## Commands and results

All commands below run after `cd /home/twidi/dev/twicc-poc/.worktrees/mcp-events &&`.
Results are recorded only after execution completes.

| Command | Result |
|---|---|
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_catalog.py tests/test_mcp_events_verification.py tests/test_mcp_pinned_https.py tests/test_mcp_events_methods.py` | Initial new tests: 197 passed, 2 test-assumption failures; corrected below. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_verification.py::test_all_cancelled_callers_leave_no_unhandled_failure tests/test_mcp_events_methods.py::test_combined_invalid_schema_secret_url_lookup_precedence` | 5 passed. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_methods.py tests/test_mcp_events_acceptance.py` | 136 passed. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_commands.py -k hybrid_watcher --tb=short` | Before import restoration: 2 passed, 1 failed, 18 deselected. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_commands.py --tb=short` | After restoration: 21 passed. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events*.py tests/test_mcp_pinned_https.py --tb=short` | After cumulative fixes: **495 passed**. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_methods.py tests/test_mcp_events_rebase.py --tb=short` | Final arrival/history/rebase gaps: 146 passed. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_wait_reply.py tests/test_wait_background.py tests/test_wait_default_cursor.py tests/test_cli_session_wait.py tests/test_cli_sessions_wait_reply.py tests/test_send_messages_wait.py tests/test_codex_rollout_migration.py --tb=short` | **291 passed**. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp*.py --tb=short` | **730 passed**, including final event additions, OAuth, CIMD, batch, tools, startup, and internal isolation. |
| `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_hardcoded_commands.py tests/test_history_facts.py tests/test_codex_recompute_persistence.py tests/test_watcher_source_identity.py tests/test_watcher_catch_up.py tests/test_live_sync_slices.py --tb=short` | **78 passed**. |

Review fix validation: `TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_mcp_events_recovery.py tests/test_mcp_events_runtime.py --tb=short` reports **43 passed**.
The added case exercises a real subscribe committed after the supervisor reload snapshot.
Its actual on-commit AddCommand creates the absent monitor; the persisted subscription remains intact.
The earlier update-retention test remains separate. Focused Ruff and `git diff --check` also pass.

The changed-file lint command builds its file list from `git diff --name-only f8839ac0 -- '*.py'`.
It adds untracked Python files and executes `uvx ruff check` on those paths.
The initial check reports six feature-scope findings, including the two missing watcher names.
After corrections: **all 45 changed Python files pass**. No unrelated lint changes occur.
`git diff --check` passes. Every named test reference in this document resolves to a test function.
No unresolved integration failure warrants a full repository suite. The optional real-binary Codex migration suite is not run.

## Complete spec §12 scenario map

References use `module::test_name`. Unless stated otherwise, `module` means `tests/test_mcp_events_<module>.py`.
`pinned` means `tests/test_mcp_pinned_https.py`; `cli` means `tests/test_cli_sessions_wait_reply.py`.
Parameterized cases are part of each named test. Multiple references identify complementary boundary checks.
The map follows §12 order and expands compound bullets into separate scenarios.

### Identity, validation, and verification arrival

| Spec scenario | Passing test reference |
|---|---|
| Deterministic subscription ID; connection isolation; key order; cursor exclusion | `catalog::test_subscription_identity_exact_digest`; `catalog::test_identity_defaults_key_order_and_cursor_independence`; `catalog::test_identity_connection_url_and_name_sensitivity` |
| Every subscribe error code and data; unknown keys and `_meta` | `methods::test_subscribe_validation_order`; `methods::test_create_result_snapshot_and_audit`; `methods::test_verification_failure_changes_nothing`; `methods::test_limit_precheck_skips_verification_and_foreign_rows_do_not_count` |
| Unpadded secret; invalid alphabet/type/size; successful size boundaries | `methods::test_invalid_secret`; `methods::test_missing_secret_fails_before_lookup_or_verification`; `methods::test_ttl_and_unpadded_secret_and_integral_float_cursor`; `methods::test_secret_size_boundaries_succeed` |
| Cursor above signed-32-bit maximum; invalid TTL before verification | `methods::test_subscribe_validation_order` |
| Combined schema → secret → URL → lookup validation order | `methods::test_combined_invalid_schema_secret_url_lookup_precedence` |
| Refresh becomes dormant during verification; same memory and fresh wait; delivers again | `methods::test_timely_refresh_wakes_dormant_monitor_with_fresh_wait_and_delivers`; `runtime::test_refresh_preserves_live_state_and_dormant_wake_creates_fresh_wait` |
| Refresh row removed during verification becomes a new generation | `methods::test_deleted_during_verification_refresh_rechecks_lookup_flag` |
| Concurrent identical new subscribes converge on one generation; duplicate add updates one monitor | `methods::test_concurrent_identical_inserts_reclassify_to_one_generation`; `runtime::test_generation_commands_replace_only_add_and_ignore_stale_update_remove` |
| Same expired identity recreates at cap; foreign rows do not count | `methods::test_expired_or_foreign_identity_replaced_at_limit`; `methods::test_limit_precheck_skips_verification_and_foreign_rows_do_not_count` |
| Refresh during retry and queued turn write preserves memory; no duplicate or ended | `methods::test_refresh_with_queued_turn_write_preserves_monitor_and_delivers_again`; `runtime::test_refresh_preserves_live_state_and_dormant_wake_creates_fresh_wait` |
| Reply during verification uses arrival L0 and delivers | `methods::test_verification_keeps_arrival_cursor_but_observes_insert_time_history[reply]`; `methods::test_verification_arrival_race_reaches_real_detector_and_delivers[reply]` |
| Prompt/crash during verification opens history turn and delivers ended | The same two tests with `[prompt_crash]` |
| Non-string/non-ASCII echo and lone-surrogate JSON fail safely | `verification::test_closed_response_reasons`; `verification::test_non_ascii_matching_challenge` |
| Real sockets: handshake/read/outer timeouts, plain HTTP TLS error, ClientHello EOF, certificate failure | `pinned::test_real_socket_exception_chains`; retry decision in `delivery::test_status_and_error_retry_matrix_keeps_subscription` |
| Non-ASCII callback hostname fails validation | `methods::test_invalid_url` |
| TTL grants; refresh never changes detection cursor | `methods::test_ttl_and_unpadded_secret_and_integral_float_cursor`; `methods::test_refresh_preserves_every_detection_field_and_rotates_secrets` |
| Both capability forms on actual external SDK discovery | `external::test_authenticated_discovery_custom_results_legacy_and_internal_isolation` |

### Payload and Rules A/B

| Spec scenario | Passing test reference |
|---|---|
| CLI reply shape without waited_seconds; title; request_type; empty provider error; schema validation | `payload::test_exact_data_schema_and_text_presence`; `catalog::test_payload_contract` |
| Maximal text prefix within 262144 bytes, including Unicode and escapes | `payload::test_truncation_is_maximal_complete_json_and_does_not_mutate`; `payload::test_exact_limit_does_not_add_truncation_marker` |
| Exact signature bytes, integer header timestamp, two-key window | `signing::test_five_headers_sign_exact_body_and_same_integer_timestamp`; `signing::test_rotation_signs_both_keys_only_before_exact_deadline`; `delivery::test_rotation_between_attempts_uses_current_secrets_and_window` |
| Idle never emits ended | `turns::test_idle_end_is_dropped_without_extra_snapshot_and_wait_is_kept` |
| Reply while working then idle has no ended | `turns::test_conclusions_always_emit_then_close_without_followup_end` |
| Awaiting then stop emits exactly one ended, carrying an older message | `turns::test_awaiting_stop_carries_old_message_but_closes_at_tick_time` |
| Hybrid local command outside a turn has no ended | `turns::test_transition_guard_emits_real_turns_and_closes_pseudo_turns`; `commands::test_hybrid_watcher_preserves_prompt_command_and_interruption_signals` |
| Ended closes a turn; no second ended after flush | `turns::test_known_open_turn_without_prompt_emits_end`; `turns::test_ended_reference_blocks_old_crash_prompt_on_next_pseudo_turn` |
| Two pending requests: one per tick, then none; cursor/L0 jumps cannot repeat requests | `turns::test_pending_one_per_tick_in_order_survives_cursor_and_keeps_first` |
| Answer before a block delivers replied first | `turns::test_transcript_precedes_pending_then_awaiting_origin_survives_turn` |
| History with no real prompt stays closed; real prompt/crash opens ended | `methods::test_history_cursor_opens_only_for_real_prompt_and_crash` |
| Three past conclusions produce one past event, then new events | `turns::test_history_delivers_one_old_conclusion_then_only_new_lines` |
| Dropped ended does not contaminate next crash | `turns::test_dropped_end_message_is_not_carried_into_new_turn_crash` |
| Idle gap shorter than tick still observes second transition | `turns::test_idle_gap_between_ticks_still_opens_next_turn` |
| Watcher lag before/after second turn dies preserves second ended | `turns::test_late_conclusion_preserves_latest_turn_except_history_before_initial_jump`; `turns::test_watcher_lag_answer_arrives_after_second_turn_already_stopped` |
| Same-state reset does not open a turn | `turns::test_transition_requires_each_state_and_timestamp_condition`; `acceptance::test_unobserved_turn_transition_has_no_ended` |
| Two crashes without assistant lines have distinct IDs when their user lines differ | `turns::test_two_crashes_without_assistant_lines_have_distinct_ids` |

### Background work and turn references

| Spec scenario | Passing test reference |
|---|---|
| Ignored final then no-background final: only second replied | `background::test_ignored_final_then_final_without_background_delivers_only_second` |
| Ignored final, idle shell, reopened turn: cursor passes ignored line and delivers reopened answer | `background::test_idle_background_then_reopened_turn_resumes_after_ignored_final` |
| Work ends without answer: silence, then next crashed turn emits ended | `background::test_work_ends_without_answer_stays_silent_then_new_crash_emits` |
| Agent dies after ignored final: ended carries ignored text | `background::test_agent_death_delivers_ignored_message_with_tick_time_and_closes` |
| Dropped non-final ended silently moves cursor; next line-less crash has no text | `turns::test_dropped_end_message_is_not_carried_into_new_turn_crash` |
| Pseudo-turns: hybrid command/adoption, Codex compact/goal clear/plan; real prompt passes | `turns::test_transition_guard_emits_real_turns_and_closes_pseudo_turns`; `turns::test_codex_injected_command_guard_drops_pseudo_turn`; `commands::test_codex_injected_and_rewritten` |
| Resumed process dies before any transcript line: dead-agent guard passes | `turns::test_transition_guard_emits_real_turns_and_closes_pseudo_turns`; `turns::test_no_row_ended_uses_epoch_zero_and_retains_guard_reference` |
| Interrupted turn's flush survives pseudo-transition without changing its reference | `turns::test_interrupted_open_turn_keeps_origin_during_pseudo_transition`; `turns::test_second_working_transition_preserves_open_turn_origin_and_guard` |
| Reopened turn asks question then stops: awaiting, then ended | `background::test_reopened_turn_question_then_stop_emits_awaiting_and_ended` |
| Every new turn gets a fresh full flush window | `turns::test_stop_between_transition_and_step_gets_full_new_flush_window` |
| Hybrid transition stamped after own final; pseudo-transition before late final: one reply, no ended | `turns::test_hybrid_late_transition_answer_does_not_emit_empty_end`; `turns::test_late_conclusion_preserves_latest_turn_except_history_before_initial_jump` |
| Pseudo-turn during ignored background state closes old work and stays silent | `background::test_pseudo_turn_during_ignored_state_closes_old_work_before_guard` |
| cursor_at never regresses for old carried message or non-monotonic timestamps | `turns::test_missing_timestamp_uses_tick_start_and_cursor_time_never_decreases`; `turns::test_awaiting_stop_carries_old_message_but_closes_at_tick_time`; `writer::test_cursor_fields_advance_independently_when_deliveries_finish_out_of_order` |

### Persistence, failure containment, and supervisor

| Spec scenario | Passing test reference |
|---|---|
| Open turn survives shutdown and dead agent emits ended after restart | `recovery::test_actual_ended_shutdown_boundary_and_restart` |
| Older retrying reply re-detected after restart keeps later open turn and emits its ended | `acceptance::test_restart_pending_request_reuses_id_and_older_reply_keeps_later_turn_open` |
| Old-generation retry and cursor cannot touch replacement subscription | `delivery::test_retry_rechecks_authority_and_cannot_write_recreated_generation`; `writer::test_old_generation_cannot_mutate_replacement_row` |
| Shutdown drains turn writes; last-tick ended drop preserves open state for restart | `recovery::test_actual_ended_shutdown_boundary_and_restart`; `recovery::test_shutdown_join_is_nonblocking_and_dead_writer_drains` |
| Supervisor barrier follows dead thread's writes but excludes later writes; existing update survives | `recovery::test_supervisor_barrier_recovers_dead_writer_and_retains_committed_commands`; `recovery::test_barrier_finishes_without_waiting_for_later_writes` |
| New subscription commits after supervisor load snapshot; its AddCommand creates the absent monitor | `recovery::test_new_subscription_committed_after_supervisor_load_survives_rebuild` |
| Timestamp/guard/payload/fit exception after step retries same ID; no fallible work after posting | `recovery::test_post_step_failure_replays_same_id_from_unchanged_cursor`; `recovery::test_guard_failure_preserves_open_turn_then_emits_end`; `recovery::test_posted_emission_has_no_later_database_or_payload_work` |
| Persistent step failure: bounded logs, capped backoff, skip after three, later answer delivered | `recovery::test_persistent_step_failure_bounds_logs_backoff_and_does_not_sleep_other_monitors`; `recovery::test_poison_batch_is_skipped_after_three_failures_and_later_answer_emits` |
| Boot raw final: no false ended until compute catches up, then replied | `readiness::test_compute_commits_after_unclassified_scan_does_not_emit_false_end`; `readiness::test_unready_end_preserves_turn_wait_and_timer_until_two_ready_reads` |
| Renewal never regresses turn_start_line when replaying older reply | `turns::test_earlier_conclusion_never_moves_guard_reference_back` |
| Persistent awaiting failure marks bad request reported and continues with next | `recovery::test_persistent_pending_request_is_reported_lost_then_next_request_emits` |
| Writer item failure does not stop queue; barrier resolves | `writer::test_writer_survives_sql_error_and_commits_turns_in_producer_order`; `recovery::test_failed_writer_item_still_resolves_supervisor_barrier` |
| Dead writer restarts on same queue before/during rebuild barrier | `writer::test_restarted_writer_consumes_same_queue_and_cancelled_barrier_is_safe`; `recovery::test_supervisor_barrier_recovers_dead_writer_and_retains_committed_commands`; `recovery::test_writer_dies_during_same_barrier_and_recovers` |
| Dead writer restarts and drains at shutdown | `recovery::test_shutdown_join_is_nonblocking_and_dead_writer_drains` |
| Runtime isolates failing monitor; thread restarts; existing-id add updates | `recovery::test_persistent_step_failure_bounds_logs_backoff_and_does_not_sleep_other_monitors`; `recovery::test_real_worker_initial_load_failure_is_supervised_and_closes_connections`; `runtime::test_generation_commands_replace_only_add_and_ignore_stale_update_remove` |
| Concurrent distinct new identities cannot exceed caps | `methods::test_authoritative_limit_blocks_concurrent_new_identity` |

### Rebase, readiness, and lookup races

| Spec scenario | Passing test reference |
|---|---|
| Restart pending rebase; compute failure then reload; consumed/lost wake-up recovery | `rebase::test_load_and_reload_recover_arrival_before_begin_or_mid_replacement`; `rebase::test_lost_wakeup_backstop_detects_cursor_past_shorter_history` |
| Crash between replacement begin/finish; boot repair waits for current compute | `rebase::test_partial_replacement_repair_keeps_pending_until_current_compute` |
| Dormant rebase applies before next scan | `rebase::test_dormant_command_reads_but_wake_applies_before_scanning` |
| Two rebuilds around CAS preserve numbering; old-numbering writes do not move line | `rebase::test_two_overlapping_rebases_keep_fifo_cas_and_reject_old_cursor_lines`; `writer::test_rebase_cas_can_lower_cursor_and_old_epoch_only_advances_time` |
| Rebuild lookup allows epoch history; CLI unchanged; live refresh survives ordinary failed lookup | `lookup::test_rejected_row_preserved`; `lookup::test_epoch_requires_creation`; `lookup::test_cli_rejected_live_row_starts_at_zero`; `methods::test_lookup_failure_allows_live_refresh_but_rejects_new`; `methods::test_arrival_numbering_and_epoch_acceptance` |
| Verification overlaps begin, or arrives mid-replacement with NULL numbering | `methods::test_subscribe_verification_overlaps_rebuild_and_queued_turn`; `rebase::test_load_and_reload_recover_arrival_before_begin_or_mid_replacement` |
| Stale Claude compute does not trigger rebase or discard supplied cursor | `acceptance::test_claude_stale_compute_keeps_supplied_history_cursor` |
| Reused line in new numbering has another ID; generation is not identity | `payload::test_line_identity_includes_epoch_and_is_stable`; `catalog::test_event_identity_exact_digest_and_keys`; `methods::test_recreated_generation_replays_original_cursor_and_delivers_same_id` |
| Epoch-mismatched emission is suppressed | `rebase::test_final_snapshot_epoch_mismatch_suppresses_every_emission_phase_write` |
| Rebase closes old open turn and skips rebuild-window conclusions | `rebase::test_pending_rebase_reads_once_per_tick_then_moves_all_lines_to_new_end`; `acceptance::test_rebuild_discards_intermediate_conclusions_then_delivers_new_epoch` |
| Shorter and longer rebuilt histories skip old answers; next answer delivers; late old cursor stays stale | `acceptance::test_rebuild_discards_intermediate_conclusions_then_delivers_new_epoch`; `writer::test_rebase_cas_can_lower_cursor_and_old_epoch_only_advances_time` |
| No Session row counts ready; line-less ended is evaluated normally | `readiness::test_no_previous_read_is_unready_but_missing_session_read_is_ready`; `turns::test_no_row_ended_uses_epoch_zero_and_retains_guard_reference` |
| Idle monitor only reads readiness at five-second backstop | `readiness::test_idle_ticks_only_read_snapshot_at_five_second_backstop` |
| Live subscribe before row uses numbering 0; row appearance does not rebase; first reply delivers | `methods::test_live_without_row_and_rejected_live_row_keep_arrival_metadata`; `acceptance::test_no_row_creation_then_row_appearance_delivers_first_reply_without_rebase` |
| Death before row emits ended; fresh Codex offset=0/epoch=0 stays numbering 0 | `turns::test_no_row_ended_uses_epoch_zero_and_retains_guard_reference`; `methods::test_arrival_numbering_and_epoch_acceptance` |
| Begin between scan and final read suppresses all emission-phase writes, including readiness/guard drops | `rebase::test_final_snapshot_epoch_mismatch_suppresses_every_emission_phase_write`; `rebase::test_new_turn_writes_precede_rebase_and_are_superseded` |
| Compute after unclassified scan: no ended, then replied | `readiness::test_compute_commits_after_unclassified_scan_does_not_emit_false_end` |
| Ready/raw-insert/scan/compute/ready: changed last_line withholds ended | `readiness::test_new_raw_line_then_compute_between_ready_reads_blocks_end` |
| Agent already working when rebase applies opens initial turn and later emits ended | `rebase::test_rebase_opens_current_work_even_when_transition_already_observed`; `acceptance::test_working_turn_at_rebase_apply_later_crashes_and_emits_ended`; `methods::test_subscribe_verification_overlaps_rebuild_and_queued_turn` |
| Missing-row snapshot compares as ready/epoch0/last_line0 | `readiness::test_no_previous_read_is_unready_but_missing_session_read_is_ready`; `turns::test_no_row_ended_uses_epoch_zero_and_retains_guard_reference` |
| Rebase CAS actually raises; new numbering still delivers but durable cursor freezes; supervisor/restart heals without replay | `acceptance::test_failed_rebase_cas_then_delivered_conclusion_heals_without_replay[supervisor]`; same test `[restart]` |
| Refresh lookup fails and row disappears before final classification: unknown_session, no insertion | `methods::test_deleted_during_verification_refresh_rechecks_lookup_flag[True]` |

### Dormancy, replay, transport, and remaining protocol checks

| Spec scenario | Passing test reference |
|---|---|
| Dormant complete turn cannot cause premature ended; fresh wait delivers late final | `acceptance::test_dormant_turn_and_answered_request_are_lost_but_late_final_delivers`; `methods::test_timely_refresh_wakes_dormant_monitor_with_fresh_wait_and_delivers` |
| Cleanup removes dormant monitor; timely refresh past expiry survives margin | `external::test_cleanup_current_directory_margin_revocation_and_generation`; `methods::test_timely_refresh_uses_arrival_classification_after_expiry`; `methods::test_timely_refresh_wakes_dormant_monitor_with_fresh_wait_and_delivers` |
| Codex prompt during hold, ignored final, hold ends, compact: no ended | `acceptance::test_codex_prompt_during_hold_then_compact_does_not_emit_ended` |
| History cursor inside running turn with F1/P2/F2: only F1, no ended | `acceptance::test_history_middle_of_running_turn_closes_after_first_past_final` |
| Interrupted send without assistant line, then rename/compact: no second ended | `turns::test_ended_reference_blocks_old_crash_prompt_on_next_pseudo_turn`; `turns::test_codex_injected_command_guard_drops_pseudo_turn` |
| Initial working turn plus historical reply retains later interrupted turn's ended | `turns::test_late_conclusion_preserves_latest_turn_except_history_before_initial_jump[initial-True-transition]` |
| Restart replays unpersisted conclusion with same ID; first historical replay stays bounded | `acceptance::test_restart_replays_only_first_past_conclusion_with_same_id` |
| Slow retry never regresses durable cursor | `delivery::test_slow_completion_preserves_newer_cursor_and_independent_time`; `writer::test_cursor_fields_advance_independently_when_deliveries_finish_out_of_order` |
| Unsubscribe and expiry stop retries | `delivery::test_retry_rechecks_authority_and_cannot_write_recreated_generation` |
| Status retry matrix; 410/413/other 4xx never retry; 410 retains subscription | `delivery::test_status_and_error_retry_matrix_keeps_subscription` |
| Revocation deletes; empty external base URL retains | `delivery::test_authority_suppresses_and_only_deletes_revoked_or_resource_mismatch`; `delivery::test_configuration_becoming_empty_retains_subscription_between_attempts` |
| Pinned transport rejects non-global addresses and does not follow redirects; DNS/certificate classification | `pinned::test_address_refusal_precedes_connection`; `pinned::test_pin_host_sni_port_path_query_headers_body`; `pinned::test_dns_and_non_global_are_expected_failures`; `pinned::test_real_socket_exception_chains` |
| Echo success/cache; 3xx/4xx/5xx/bad echo/timeout reasons | `verification::test_signed_shared_challenge_cache_and_expiry`; `verification::test_closed_response_reasons`; `verification::test_transport_reasons_shared_with_joiners` |
| Shared verification, joiners consume no slots; ninth-slot deadline; host rate=60 | `verification::test_signed_shared_challenge_cache_and_expiry`; `verification::test_slots_timeout_and_shutdown_cleanup`; `verification::test_host_rate_boundary_and_other_host` |
| Expired/foreign identity is new: fresh cursor, limit check, verification | `methods::test_expired_or_foreign_identity_replaced_at_limit`; `methods::test_recreated_generation_replays_original_cursor_and_delivers_same_id` |
| Unsubscribe identity, optional mode, connection isolation, idempotence, invalid params | `methods::test_unsubscribe_identity_scope_idempotence_and_generation`; `methods::test_unsubscribe_validates_identity_types` |
| Empty hostname, malformed IPv6, non-string callback URL rejected | `methods::test_invalid_url` |
| TTL absent/null/zero/negative/huge integer/clamps; 1e400, NaN, infinities, string, bool rejected | `methods::test_create_result_snapshot_and_audit`; `methods::test_ttl_and_unpadded_secret_and_integral_float_cursor`; `methods::test_subscribe_validation_order` |
| Integral float cursor stored as integer | `methods::test_ttl_and_unpadded_secret_and_integral_float_cursor` |
| Expired recreation before cleanup creates fresh generation/monitor and delivers | `methods::test_recreated_generation_replays_original_cursor_and_delivers_same_id` |
| Supervisor reload during hold preserves float transition and stays closed | `acceptance::test_supervisor_reload_after_reply_during_hold_does_not_reopen_float_transition` |
| New-generation add replaces; old remove does not stop replacement | `runtime::test_generation_commands_replace_only_add_and_ignore_stale_update_remove` |
| Retry after secret rotation signs with current key | `delivery::test_rotation_between_attempts_uses_current_secrets_and_window`; `signing::test_retry_uses_new_authority_secret_and_new_timestamp_with_same_body` |
| Audit exactly once on creation/deletion, never refresh | `methods::test_create_result_snapshot_and_audit`; `methods::test_refresh_preserves_every_detection_field_and_rotates_secrets`; `external::test_real_handlers_deliver_without_browser_audit_and_preserve_tools` |
| Runtime/cleanup ignore foreign data_dir | `runtime::test_load_only_current_live_unrevoked_rows_and_restore_persisted_state`; `external::test_cleanup_current_directory_margin_revocation_and_generation` |
| All outcome data validates with jsonschema | `payload::test_exact_data_schema_and_text_presence`; `catalog::test_payload_contract` |

## Accepted limits and tested consequences

This feature provides **bounded best-effort delivery**. It provides neither guaranteed delivery nor complete transcript replay.
There is no persistent outbox. Verification and retries live in memory.

| Limit | Consequence and evidence |
|---|---|
| Bounded retries | Three attempts at delays 0, 30, and 120 seconds. Exhaustion consumes the cursor. `delivery::test_status_and_error_retry_matrix_keeps_subscription`. |
| Restart loses pending retries | Transcript past the durable cursor can replay with the same ID. A later/silent cursor can suppress that replay. `acceptance::test_restart_replays_only_first_past_conclusion_with_same_id`; `acceptance::test_retry_conclusion_passed_by_silent_cursor_is_not_replayed`. |
| Admitted ended can be lost across restart | Its turn-closed write can commit before delivery finishes. Cancellation does not persist its cursor, but restart sees the closed turn. `recovery::test_actual_ended_shutdown_boundary_and_restart[running-delivery]`. |
| Last-tick shutdown drop | Admission rejects both emission and related closed-turn write. Restart can emit the ended. The same test covers `[during-fit]` and `[queued-callback]`. |
| Expired or unconfigured authority | No POST occurs, but the cursor still persists. The conclusion is lost. `delivery::test_authority_suppresses_and_only_deletes_revoked_or_resource_mismatch` checks actual cursor persistence for these cases. |
| Dormancy | Transcript replies can arrive late. Unobserved ended and already-answered requests disappear. `acceptance::test_dormant_turn_and_answered_request_are_lost_but_late_final_delivers`. |
| No replay flood | `since_line_num` yields at most one past conclusion, then skips to arrival L0. `turns::test_history_delivers_one_old_conclusion_then_only_new_lines`; `acceptance::test_history_middle_of_running_turn_closes_after_first_past_final`. |
| Recreation repeats original arguments | Expired/foreign recreation can replay an old answer with the same event ID. Receiver deduplication retention remains unknown. `methods::test_recreated_generation_replays_original_cursor_and_delivers_same_id`. |
| Identity excludes starting cursor | Refreshing `since_line_num` does not create another identity or rewind the cursor. `catalog::test_identity_defaults_key_order_and_cursor_independence`; `methods::test_refresh_preserves_every_detection_field_and_rotates_secrets`. |
| Two turns write no lines | Their line-less ended IDs can match. `acceptance::test_two_turns_without_any_lines_share_ended_id`. User-line-writing crashes have distinct IDs. |
| Data-dir binding | Different paths are foreign. A restored copy at the same path can deliver like the original. `runtime::test_load_only_current_live_unrevoked_rows_and_restore_persisted_state`; `delivery::test_same_path_restored_instance_can_deliver_the_same_event`. |
| Rebase window | Conclusions during rebuild are discarded. The supplied cursor becomes the new history end. Longer/shorter histories both obey this rule. `acceptance::test_rebuild_discards_intermediate_conclusions_then_delivers_new_epoch`; `methods::test_subscribe_verification_overlaps_rebuild_and_queued_turn`. |
| Old cursor supplied after rebuild | A client cursor has no epoch tag. It is interpreted in the current numbering and can select one wrong past conclusion. `catalog::test_identity_defaults_key_order_and_cursor_independence` and the history tests pin the untagged protocol and bounded replay. |
| Compute stays stale | Ended stays withheld; pending rebase withholds every outcome until compute is current. `readiness::test_unready_end_preserves_turn_wait_and_timer_until_two_ready_reads`; `rebase::test_partial_replacement_repair_keeps_pending_until_current_compute`. |
| Raw boot lines | A later classified answer can move the cursor past an earlier raw answer. `acceptance::test_raw_boot_final_can_be_skipped_by_later_classified_final`. |
| Ignored background answer | No new answer means no event while the agent lives. Replacing the wait can re-read and deliver the ignored answer. `background::test_work_ends_without_answer_stays_silent_then_new_crash_emits`; `acceptance::test_ignored_background_final_replays_after_wait_replacement`. |
| Persistent replied/provider_error failure | Three failures skip the bad batch; its conclusion is lost. `recovery::test_poison_batch_is_skipped_after_three_failures_and_later_answer_emits`. |
| Persistent awaiting failure | Three failures mark that request reported without sending it. `recovery::test_persistent_pending_request_is_reported_lost_then_next_request_emits`. |
| Persistent ended failure | The turn stays open and retries at capped backoff until progress or a new turn. `recovery::test_unseeded_ended_failures_preserve_series_through_reconstruction`. |
| Failed state write | Memory can advance while durable state remains stale. Failed rebase CAS heals at next load by skipping to the current end. `acceptance::test_failed_rebase_cas_then_delivered_conclusion_heals_without_replay`; `writer::test_writer_survives_sql_error_and_commits_turns_in_producer_order`. |
| Rule A observation gaps | A same-state reset, unobserved start/stop, or turn without a transition can lose ended. `acceptance::test_unobserved_turn_transition_has_no_ended`. This rule never filters a detected replied. |
| Guard observation gaps | An alive transition-opened turn with only a system/command line can lose ended. A dead agent passes the guard. `turns::test_transition_guard_emits_real_turns_and_closes_pseudo_turns`; `turns::test_codex_injected_command_guard_drops_pseudo_turn`. |
| Pseudo-turn false positives | Initial turns and pseudo-turns whose agent dies can emit empty ended. `acceptance::test_pseudo_turn_running_at_creation_or_dead_at_end_can_emit_empty_ended`. |
| Creation after fast crash | No working process and no evidence past the supplied cursor leave the turn closed. `methods::test_history_cursor_opens_only_for_real_prompt_and_crash[none]`. |
| Answer predates creation while agent works | It is outside the new scan. An eventual ended can carry older text through the unchanged detector. CLI regression tests retain this detector behavior. |
| Hybrid adoption and cron restart | Event detection uses the provider's observed state. Adoption can expose idle before real work ends; restarted work can replace the killed turn. Existing provider behavior is unchanged. No stronger completion guarantee is added. |
| Late hybrid prompt and absent-row guard reference | A prompt already covered by turn_start_line cannot prove a later turn. An absent row cannot advance that reference. `turns::test_hybrid_late_transition_answer_does_not_emit_empty_end`; `turns::test_no_row_ended_uses_epoch_zero_and_retains_guard_reference`. |
| Temporary cap overshoot | A timely refresh crossing expiry can coexist with a newly admitted subscription. `methods::test_timely_refresh_can_overshoot_limit_after_expiry` demonstrates 51 live rows. |
| Polling and database lock latency | One worker polls monitors serially. SQLite stalls delay other monitors. Rebuilds hold the writer lease and can delay subscribe past a client timeout. No throughput or latency benchmark is claimed. |
| Residual duplicate risk | Work before posting can retry with the same ID. All expected fallible work precedes posting. `recovery::test_posted_emission_has_no_later_database_or_payload_work`. |
| Draft churn | Wire constants and schemas remain explicit and exact-tested. Future draft changes require a contract update. `catalog::test_complete_catalog_matches_authoritative_spec_json`. |

### Recovery and shutdown interpretation

A successful recovery tick must handle a conclusion or make meaningful turn, cursor, scan, or rebase progress.
A fresh wait's flush/confirmation-only tick at the same failed position does **not** reset the failure series.
Otherwise repeated ended failures continually reset their backoff and violate the persistent-failure rule.

Evidence: `recovery::test_unseeded_ended_failures_preserve_series_through_reconstruction` and
`recovery::test_ended_recovery_resets_on_meaningful_progress`.
This interpretation retains the failure series only while recovery has made no meaningful progress.

The **two-second drain deadline** bounds queue draining before writer cancellation starts.
It does not bound cancellation-shielded database write settlement.
An in-flight protected write can keep shutdown waiting beyond two seconds while it retains the database lease.

Evidence: `recovery::test_shutdown_waits_for_protected_write_settlement_after_drain_deadline` uses a real blocked write.
It verifies lease retention, final commit, and cancellation before the next queued write.
The five-second thread join and two-second drain thresholds remain separately tested.

## Manual release gate: Task 15 remains pending

The automated suite cannot validate ChatGPT's rendering, subscription decisions, refresh behavior, or feedback-loop handling.
A real Work chat must complete all eight spec §12 E2E steps before release:

1. Accept both discovery forms and show `session.concluded` on the plugin page.
2. Verify/store a subscription; subscribe to two sessions and ten sessions in separate requests.
3. Use a `replied` event's `reply.text` directly in chat.
4. Retrieve full content after `text_truncated: true`.
5. Show each new awaiting request once with the correct `request_type`.
6. Deliver a reply already past `since_line_num` immediately.
7. Refresh across a backend restart; unsubscribe; revoke the connection and confirm delivery stops.
8. Stop a conclusion-response feedback loop through both “stop monitoring” and connection revocation.

The optional draft conformance suite is not run.
Its webhook receiver needs a public HTTPS tunnel; loopback HTTP cannot pass the callback policy.
No conformance or ChatGPT E2E success is claimed.

Before deployment, the user must apply the new migration and restart the backend through `devctl.py`.
A user-requested `devctl.py start` applies pending migrations at startup.
This task does not migrate or restart the running instance.
