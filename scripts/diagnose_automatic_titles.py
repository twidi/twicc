#!/usr/bin/env python3
"""Compare chained automatic titles without opening or renaming a Session.

Only --live --yes together permit paid calls. Each check makes one hermetic
attempt, with production prompts, validation, models and timeouts. Retries and
cross-provider fallback are deliberately excluded from this provider comparison.
The production suggestion pipeline retains its existing retries and fallback.
Live checks are also flushed to OUTPUT.checks.jsonl as they complete.
"""

import argparse
import asyncio
import os
from pathlib import Path
import sys
import time

import orjson

from twicc.title_transcript import build_title_prompt, build_title_source, title_rejection_reasons


def validate_fixture(data: dict) -> None:
    """Reject malformed fixtures before any provider runs."""
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("cases must be a non-empty list")
    ids = set()
    for case in data["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("each case requires a non-empty string id")
        if case["id"] in ids:
            raise ValueError("case ids must be unique")
        ids.add(case["id"])
        messages = case.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, str) for m in messages):
            raise ValueError("messages must be a non-empty list of strings")
        counts = case.get("check_counts")
        if (not isinstance(counts, list) or not counts
                or any(type(n) is not int or not 1 <= n <= len(messages) for n in counts)
                or any(a >= b for a, b in zip(counts, counts[1:]))):
            raise ValueError("check_counts must be increasing integers within messages")
        subjects = case.get("expected_subjects")
        if not isinstance(subjects, list) or not all(isinstance(s, str) and s.strip() for s in subjects):
            raise ValueError("expected_subjects must be a list of non-empty strings")


async def call_model(provider: str, prompt: str) -> str:
    """Use the production hermetic runners; never access session models or writers."""
    if provider == "haiku":
        from twicc.providers.claude_code.hermetic import run_hermetic_claude
        from twicc.providers.claude_code.title_suggest import SUGGESTION_TIMEOUT_SECONDS

        result = await asyncio.wait_for(
            run_hermetic_claude(prompt, model="haiku"), timeout=SUGGESTION_TIMEOUT_SECONDS,
        )
        if result.is_error or result.assistant_error:
            raise RuntimeError(f"Claude error: {result.assistant_error!r}; is_error={result.is_error}")
        return result.text
    from openai_codex.generated.v2_all import ReasoningEffort
    from twicc.providers.codex.hermetic import prepare_hermetic_codex, run_hermetic_codex
    from twicc.providers.codex.title_suggest import SUGGESTION_TIMEOUT_SECONDS, TITLE_MODEL

    plan = await prepare_hermetic_codex(TITLE_MODEL)
    result = await asyncio.wait_for(
        run_hermetic_codex(plan, prompt, effort=ReasoningEffort.low), timeout=SUGGESTION_TIMEOUT_SECONDS,
    )
    if result.terminal_error is not None:
        raise RuntimeError(f"Codex error: {result.terminal_error!r}")
    return result.text


def aggregate(checks: list[dict]) -> dict:
    comparisons = [row for row in checks if row["previous_title"] is not None and row["error"] is None]
    kept = sum(row["kept"] for row in comparisons)
    return {
        "call_count": sum(row["called"] for row in checks),
        "check_count": len(checks),
        "failure_count": sum(row["error"] is not None for row in checks),
        "comparison_count": len(comparisons),
        "kept_count": kept,
        "keep_fraction": kept / len(comparisons) if comparisons else None,
    }


async def run_cases(data: dict, providers: list[str], system_prompt: str, *, call=None, progress=None) -> dict:
    """Run each provider's chain sequentially, with at most two calls globally."""
    validate_fixture(data)
    if "{text}" not in system_prompt:
        raise ValueError("system prompt requires {text}")
    call = call or call_model

    async def run_provider(provider):
        checks, notes = [], []
        for case in data["cases"]:
            current_title = None
            for count in case["check_counts"]:
                source = build_title_source(case["messages"][:count])
                row = {"provider": provider, "case_id": case["id"], "check_count": count,
                       "previous_title": current_title, "title": None, "raw_response": None,
                       "kept": False, "error": None, "called": False}
                started = time.monotonic()
                try:
                    if source is None:
                        raise ValueError("no title content")
                    prompt = build_title_prompt(system_prompt, source, current_title)
                    row["called"] = True
                    raw = await call(provider, prompt)
                    row["raw_response"] = raw
                    reasons = title_rejection_reasons(raw) if isinstance(raw, str) else ["empty response"]
                    if reasons:
                        row["error"] = "; ".join(reasons)
                    else:
                        row["title"] = raw.strip()
                        row["kept"] = current_title is not None and raw.strip() == current_title
                        current_title = raw.strip()
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["latency_seconds"] = round(time.monotonic() - started, 3)
                checks.append(row)
                if progress:
                    progress(row)
            notes.append({
                "provider": provider, "case_id": case["id"], "final_title": current_title,
                "expected_subjects": case["expected_subjects"],
                "missing_literals": [s for s in case["expected_subjects"]
                                     if s.casefold() not in (current_title or "").casefold()],
                "note": "Literal hints only; review synonyms, stable rewrites, and pivot timing manually.",
            })
        return checks, notes

    runs = await asyncio.gather(*(run_provider(provider) for provider in providers))
    checks = [row for rows, _ in runs for row in rows]
    return {"checks": checks, "aggregate": aggregate(checks),
            "providers": {provider: aggregate([r for r in checks if r["provider"] == provider])
                          for provider in providers},
            "coverage_notes": [note for _, notes in runs for note in notes]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("haiku", "luna", "all"), default="all")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    journal_path = Path(str(args.output) + ".checks.jsonl")
    if args.input.resolve() in (args.output.resolve(), journal_path.resolve()):
        parser.error("input and output must differ")
    try:
        data = orjson.loads(args.input.read_bytes())
        validate_fixture(data)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    providers = ["haiku", "luna"] if args.provider == "all" else [args.provider]
    live = args.live and args.yes
    report = {"live": live, "planned_checks": len(providers) * sum(len(c["check_counts"]) for c in data["cases"]),
              "checks": [], "aggregate": aggregate([])}
    if live:
        import django
        from django.apps import apps

        # Loading provider-contributed defaults registers model classes, but
        # neither setup nor this diagnostic queries or writes Session rows.
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
        if not apps.ready:
            django.setup()
        from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

        prompt = SYNCED_SETTINGS_DEFAULTS["titleSystemPrompt"]
        with journal_path.open("wb") as journal:
            def progress(row):
                journal.write(orjson.dumps(row) + b"\n")
                journal.flush()
                print(f"{row['provider']} {row['case_id']}:{row['check_count']} "
                      f"{'ERROR' if row['error'] else 'kept' if row['kept'] else 'changed'} "
                      f"{row['latency_seconds']}s", file=sys.stderr, flush=True)
            report.update(asyncio.run(run_cases(data, providers, prompt, progress=progress)))
        report["system_prompt"] = prompt
    args.output.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n")
    print(orjson.dumps(report["aggregate"]).decode())
    return 1 if report["aggregate"]["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
