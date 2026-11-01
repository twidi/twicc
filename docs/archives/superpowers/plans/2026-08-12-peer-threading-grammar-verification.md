# Peer Threading Grammar Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete lot 1.1 by adding every missing accepted and rejected peer-message identifier boundary vector to the existing backend and CLI parametrizations.

**Architecture:** This is a test-only coverage lot over the production grammar already implemented by lot 1. The existing tests keep their behavior assertions; explicit pytest ids make each new vector independently selectable, so verification fails when a vector is absent and fails when production handles it incorrectly.

**Tech Stack:** Python 3.13, pytest, pytest-django, Typer CLI tests, Django 6.

## Global Constraints

- Work only in `/home/twidi/dev/twicc-poc/.worktrees/peer-system` on branch `peer-system`.
- Modify only `tests/test_peer_messages.py` and `tests/test_peer_cli.py`. Do not modify production code, migrations, frontend files, documentation, skills, plugin metadata, or `CHANGELOG.md`.
- Keep the production grammar exactly `[A-Za-z0-9_][A-Za-z0-9_-]{0,39}` with `fullmatch`: ASCII letter, digit, or underscore first; ASCII letter, digit, underscore, or hyphen later; total length 1 through 40.
- Add accepted `1abc` and `abc-` independently to inbound `message_id`, inbound non-empty `reply_to`, send-service `reply_to`, and CLI `--reply-to` coverage.
- Add rejected standalone `:` independently to the same four surfaces. Keep every existing vector.
- Lot 1 contracts remain unchanged: no wire `thread_id`; no delivery-routing change; no hidden delivery target; no approval-gate change; no picker reordering; one inbox row per message; grouping remains deferred; preserve `uniq_peermessage_peer_direction_msgid` on `("peer", "direction", "message_id")`.
- The UI stays unchanged. This lot changes no production behavior.
- Run every Python command with `TWICC_DATA_DIR=$PWD` from the worktree root.
- Commit steps are declarative. The implementer follows `CLAUDE.md` and `AGENTS.md` for the commit body and trailer.

---

### Task 1: Complete the grammar boundary parametrizations

**Files:**
- Modify: `tests/test_peer_messages.py`
- Modify: `tests/test_peer_cli.py`
- Test: `tests/test_peer_messages.py`
- Test: `tests/test_peer_cli.py`

**Interfaces:**
- Consumes: none; this lot has no earlier plan task.
- Produces: no interface for a later task; the expanded regression suite is the complete lot deliverable.

- [ ] **Step 1: Prove the required cases are absent before editing**

Run from `/home/twidi/dev/twicc-poc/.worktrees/peer-system`:

```bash
TWICC_DATA_DIR=$PWD uv run --active pytest -q \
  'tests/test_peer_messages.py::test_receive_message_id_tokens_round_trip_byte_for_byte[leading-digit]' \
  'tests/test_peer_messages.py::test_receive_message_id_tokens_round_trip_byte_for_byte[trailing-hyphen]' \
  'tests/test_peer_messages.py::test_receive_identifier_tokens_round_trip_byte_for_byte[leading-digit]' \
  'tests/test_peer_messages.py::test_receive_identifier_tokens_round_trip_byte_for_byte[trailing-hyphen]' \
  'tests/test_peer_messages.py::test_receive_rejects_nonconforming_message_id_without_row[standalone-colon]' \
  'tests/test_peer_messages.py::test_receive_rejects_nonconforming_reply_to_without_child[standalone-colon]' \
  'tests/test_peer_messages.py::test_send_conforming_reply_resolves_and_reaches_wire_unchanged[leading-digit]' \
  'tests/test_peer_messages.py::test_send_conforming_reply_resolves_and_reaches_wire_unchanged[trailing-hyphen]' \
  'tests/test_peer_messages.py::test_send_service_rejects_nonconforming_reply_before_insert[standalone-colon]' \
  'tests/test_peer_cli.py::test_peer_send_conforming_reply_to_reaches_transport_unchanged[leading-digit]' \
  'tests/test_peer_cli.py::test_peer_send_conforming_reply_to_reaches_transport_unchanged[trailing-hyphen]' \
  'tests/test_peer_cli.py::test_peer_send_reply_to_rejects_nonconforming_value_before_lookup[standalone-colon]'
```

Expected: FAIL during collection because every named parameter id is absent. This is the RED signal for this test-only lot: production already has the required behavior, but the required regression cases do not exist.

- [ ] **Step 2: Add the six backend parameterization changes**

In `tests/test_peer_messages.py`, replace these two exact blocks:

```python
@pytest.mark.parametrize("token", ["A", "_abc", "a-b", "A_-z", "x" * 40])
def test_receive_message_id_tokens_round_trip_byte_for_byte(
```

```python
@pytest.mark.parametrize("token", ["A", "_abc", "a-b", "A_-z", "x" * 40])
def test_receive_identifier_tokens_round_trip_byte_for_byte(
```

with these blocks, respectively:

```python
@pytest.mark.parametrize(
    "token",
    [
        "A", pytest.param("1abc", id="leading-digit"), "_abc", "a-b",
        pytest.param("abc-", id="trailing-hyphen"), "A_-z", "x" * 40,
    ],
)
def test_receive_message_id_tokens_round_trip_byte_for_byte(
```

```python
@pytest.mark.parametrize(
    "token",
    [
        "A", pytest.param("1abc", id="leading-digit"), "_abc", "a-b",
        pytest.param("abc-", id="trailing-hyphen"), "A_-z", "x" * 40,
    ],
)
def test_receive_identifier_tokens_round_trip_byte_for_byte(
```

In both rejection lists, replace this exact sequence:

```python
        None, 7, "", ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

for `test_receive_rejects_nonconforming_message_id_without_row`, and:

```python
        7, ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

for `test_receive_rejects_nonconforming_reply_to_without_child`, with:

```python
        None, 7, "", ".", pytest.param(":", id="standalone-colon"), "..",
        "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

and:

```python
        7, ".", pytest.param(":", id="standalone-colon"), "..",
        "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

respectively. Keep each list's following `"-abc", "a.b", "a:b", "x" * 41` line unchanged.

Replace this exact send-service acceptance block:

```python
@pytest.mark.parametrize("token", ["A", "_abc", "a-b", "x" * 40])
def test_send_conforming_reply_resolves_and_reaches_wire_unchanged(
```

with:

```python
@pytest.mark.parametrize(
    "token",
    [
        "A", pytest.param("1abc", id="leading-digit"), "_abc", "a-b",
        pytest.param("abc-", id="trailing-hyphen"), "x" * 40,
    ],
)
def test_send_conforming_reply_resolves_and_reaches_wire_unchanged(
```

In `test_send_service_rejects_nonconforming_reply_before_insert`, replace:

```python
        7, ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

with:

```python
        7, ".", pytest.param(":", id="standalone-colon"), "..",
        "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

Keep the following `"-abc", "a.b", "a:b", "x" * 41` line unchanged.

- [ ] **Step 3: Add the two CLI parameterization changes**

In `tests/test_peer_cli.py`, replace this exact rejection sequence inside `test_peer_send_reply_to_rejects_nonconforming_value_before_lookup`:

```python
        ".", "..", "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

with:

```python
        ".", pytest.param(":", id="standalone-colon"), "..",
        "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
```

Keep the following `"-abc", "a.b", "a:b", "x" * 41` line unchanged.

Replace this exact acceptance block:

```python
@pytest.mark.parametrize("reply_to", ["A", "_abc", "a-b", "x" * 40])
def test_peer_send_conforming_reply_to_reaches_transport_unchanged(
```

with:

```python
@pytest.mark.parametrize(
    "reply_to",
    [
        "A", pytest.param("1abc", id="leading-digit"), "_abc", "a-b",
        pytest.param("abc-", id="trailing-hyphen"), "x" * 40,
    ],
)
def test_peer_send_conforming_reply_to_reaches_transport_unchanged(
```

- [ ] **Step 4: Run every new case by its explicit parameter id**

Run the Step 1 command again.

Expected: `12 passed`. If any vector is absent, pytest reports that exact node as not found. If production accepts `:` or rejects `1abc` or `abc-`, the corresponding existing behavior assertion fails.

- [ ] **Step 5: Run the complete focused backend and CLI suites**

Run:

```bash
TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_peer_messages.py tests/test_peer_cli.py -q
```

Expected: `177 passed`. This catches collateral regressions in the existing peer-message and CLI behavior while the 12-test increase proves every required vector was collected.

- [ ] **Step 6: Run lint and scope verification**

Run after the reviewed plan is committed:

```bash
TWICC_DATA_DIR=$PWD uvx ruff check --select E4,E7,E9,F tests/test_peer_messages.py tests/test_peer_cli.py
git diff --check
{
  git diff --name-only
  git diff --cached --name-only
  git ls-files --others --exclude-standard
} | sort -u
```

Expected: Ruff and `git diff --check` exit 0. The sorted scope list prints exactly `tests/test_peer_cli.py` and `tests/test_peer_messages.py`; any production, frontend, migration, documentation, skill, plugin, or changelog path is a lot-scope failure.

- [ ] **Step 7: Commit**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:
- `tests/test_peer_messages.py`
- `tests/test_peer_cli.py`

Subject: `test(peer): cover message id grammar boundaries`
