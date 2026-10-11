"""Compare provider-global dictionaries built from public schema metadata only.

Tool results, reasoning blocks, and user/assistant prose never enter dictionary inputs.
Evaluation uses exact real contents in memory. No private content is persisted.
Run with: uv run --with zstandard python scripts/benchmark_public_schema_dictionaries.py \
    --db /path/to/data.sqlite --output /path/to/new-directory
"""

import argparse
import hashlib
import random
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

import orjson
import zstandard as zstd

DROP = object()
BLOCKED_TYPES = {
    "text",
    "input_text",
    "output_text",
    "thinking",
    "redacted_thinking",
    "reasoning",
    "tool_result",
    "tool-result",
    "function_call_output",
    "custom_tool_call_output",
    "user_message",
    "agent_message",
    "assistant_message",
    "agent_reasoning",
    "mcp_tool_call_end",
    "exec_command_end",
    "web_search_end",
}
BLOCKED_FIELDS = {
    "text",
    "thinking",
    "reasoning",
    "summary",
    "output",
    "stdout",
    "stderr",
    "signature",
    "encrypted_content",
    "tool_result",
    "tool_result_content",
    "toolUseResult",
    "result",
    "error",
    "prompt",
    "instructions",
    "description",
    "body",
    "completion",
    "message_text",
}


def public_schema(policy_path):
    """Load the explicit reviewed policy; never discover allowances from source or data."""
    policy = orjson.loads(policy_path.read_bytes())
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported public dictionary policy version")
    for provider in ("claude_code", "codex"):
        if provider not in policy["providers"]:
            raise ValueError(f"Missing provider policy: {provider}")
        config = policy["providers"][provider]
        keys = config["keys"]
        enums = config["enums"]
        if len(keys) != len(set(keys)) or set(keys) & BLOCKED_FIELDS:
            raise ValueError(f"Duplicate or forbidden keys: {provider}")
        if not set(enums) <= set(keys):
            raise ValueError(f"Enum fields must be allowed keys: {provider}")
        if any(not isinstance(value, str) for values in enums.values() for value in values):
            raise ValueError(f"Enum values must be strings: {provider}")
    return policy


def schema_only(obj, keys, constants, field=None, depth=0):
    if depth > 20:
        return DROP
    if isinstance(obj, dict):
        typ = obj.get("type")
        if isinstance(typ, str):
            if (
                typ.lower() in BLOCKED_TYPES
                or "reason" in typ.lower()
                or "thinking" in typ.lower()
                or typ.lower().endswith(("_output", "_result", "_end"))
            ):
                return DROP
            if typ not in constants.get("type", []):
                return DROP  # Unknown typed blocks fail closed.
        if isinstance(obj.get("role"), str) and obj["role"].lower() in {"tool", "function"}:
            return DROP
        result = {}
        for key, value in list(obj.items())[:128]:
            if key not in keys or key in BLOCKED_FIELDS or "thinking" in key or "reasoning" in key:
                continue
            if key in {"content", "message"} and isinstance(value, str):
                continue  # Free prose; never inspect its value or length.
            if key == "name" and (not isinstance(value, str) or value not in constants.get("name", [])):
                continue
            clean = schema_only(value, keys, constants, key, depth + 1)
            if clean is DROP:
                if (key == "payload" and typ in {"response_item", "event_msg"}) or (
                    key == "item" and typ == "item_completed"
                ):
                    return DROP
                continue
            result[key] = clean
        return result
    if isinstance(obj, list):
        result = []
        for value in obj[:16]:
            clean = schema_only(value, keys, constants, field, depth + 1)
            if clean is not DROP:
                result.append(clean)
        return result
    if isinstance(obj, str):
        if obj in constants.get(field, []):
            return obj  # A literal already present in public source, never arbitrary prose.
        if field in {"timestamp", "created_at", "updated_at"}:
            return "2000-01-01T00:00:00.000Z"
        if field in {"id", "uuid", "sessionId", "session_id", "call_id", "tool_use_id"}:
            return "00000000-0000-4000-8000-000000000000"
        if field == "arguments":
            return "{}"
        return "example"
    if isinstance(obj, bool):
        return False
    if isinstance(obj, int):
        return 0
    if isinstance(obj, float):
        return 1.5
    return None


def generic_values(obj):
    """Remove public scalar strings too, for a paired synthetic-only baseline."""
    if isinstance(obj, dict):
        return {key: generic_values(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [generic_values(value) for value in obj]
    return "example" if isinstance(obj, str) else obj


def snippets(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            prefix = orjson.dumps(key) + b":"
            if isinstance(value, (dict, list)):
                yield prefix + (b"{" if isinstance(value, dict) else b"[")
                yield from snippets(value)
            else:
                yield prefix + orjson.dumps(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from snippets(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finalize", type=Path, help="Freeze only sanitized templates_raw_16384 release assets")
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("content_dictionary_policy.json"))
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--sample-ids", type=int, default=60000)
    args = parser.parse_args()
    if not args.db.is_file() or args.sample_ids < 1:
        parser.error("A source DB and positive sample count are required")
    args.output.mkdir(parents=True, exist_ok=False)
    policy = public_schema(args.policy)
    keys = set(policy["providers"]["claude_code"]["keys"])
    constants = policy["providers"]["claude_code"]["enums"]
    # Sensitive scalar values and whole excluded blocks must be absent, even with public enum values.
    probe = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "PRIVATE_TEXT"},
                {"type": "thinking", "thinking": "PRIVATE_THINKING"},
                {"type": "tool_result", "content": {"PRIVATE_KEY": "PRIVATE_RESULT"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "PRIVATE_COMMAND"}},
            ],
        },
    }
    clean = schema_only(probe, keys, constants)
    assert b"PRIVATE" not in orjson.dumps(clean)
    assert b"tool_result" not in orjson.dumps(clean)
    assert b"thinking" not in orjson.dumps(clean)
    assert len(clean["message"]["content"]) == 1
    rng = random.Random(args.seed)
    db = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("begin")
    lo, hi = db.execute("select min(id), max(id) from core_sessionitem").fetchone()
    if lo is None:
        parser.error("The item table is empty")
    ids = rng.sample(range(lo, hi + 1), min(args.sample_ids, hi - lo + 1))
    records = {}
    for offset in range(0, len(ids), 500):
        batch = ids[offset : offset + 500]
        for sid, provider, project, raw in db.execute(
            "select i.session_id,s.provider,s.project_id,i.content from core_sessionitem i "
            "join core_session s on s.id=i.session_id where i.id in (" + ",".join("?" * len(batch)) + ")",
            batch,
        ):
            if raw:
                group = ("project", project) if project is not None else ("session", sid)
                records.setdefault(provider, []).append((group, raw.encode()))
    db.close()
    results = {
        "seed": args.seed,
        "random_id_target": args.sample_ids,
        "zstandard": zstd.__version__,
        "split": "project-disjoint; session fallback for NULL project; raw-hash-disjoint",
        "training": "no per-session cap; exclude prose, reasoning and whole tool-result blocks; source-public enum literals only",
        "policy_sha256": hashlib.sha256(args.policy.read_bytes()).hexdigest(),
        "providers": {},
    }
    release_entries = []
    for provider, values in sorted(records.items()):
        if provider not in policy["providers"]:
            continue
        keys = set(policy["providers"][provider]["keys"])
        constants = policy["providers"][provider]["enums"]
        groups = sorted({group for group, _ in values})
        rng.shuffle(groups)
        train_groups = set(groups[: max(1, int(0.7 * len(groups)))])
        train_raw = [raw for group, raw in values if group in train_groups]
        hashes = {hashlib.sha256(raw).digest() for raw in train_raw}
        evaluation = [
            raw for group, raw in values if group not in train_groups and hashlib.sha256(raw).digest() not in hashes
        ]
        rng.shuffle(evaluation)
        evaluation = evaluation[:5000]
        if not evaluation:
            results["providers"][provider] = {"skipped": "No project-disjoint evaluation"}
            continue
        templates, fragments, generic = Counter(), Counter(), Counter()
        dropped = 0
        for raw in train_raw:
            try:
                obj = orjson.loads(raw)
            except orjson.JSONDecodeError:
                dropped += 1
                continue
            clean = schema_only(obj, keys, constants)
            if clean is DROP or not clean:
                dropped += 1
                continue
            encoded = orjson.dumps(clean)
            if len(encoded) >= 8:
                templates[encoded] += 1
                generic[orjson.dumps(generic_values(clean))] += 1
                fragments.update(snippets(clean))
        unique = list(templates)
        weighted = [sample for sample, count in templates.items() for _ in range(min(count, 8))]
        # Place frequent public fragments near the end, where dictionary matches are cheaper.
        tokens = b"".join(fragment + b"," for fragment, _ in sorted(fragments.items(), key=lambda item: item[1]))
        info = {
            "sampled_rows": len(values),
            "sampled_groups": len(groups),
            "training_rows": len(train_raw),
            "training_groups": len(train_groups),
            "evaluation_rows": len(evaluation),
            "evaluation_raw_bytes": sum(map(len, evaluation)),
            "dropped_training_rows": dropped,
            "unique_templates": len(unique),
            "unique_template_bytes": sum(map(len, unique)),
            "weighted_samples": len(weighted),
            "variants": {},
        }
        candidates = [("none", None, None)]
        for size in (16384, 32768, 65536, 131072):
            ranked = b"".join(sample for sample, _ in sorted(templates.items(), key=lambda item: item[1]))
            candidates.extend(
                [
                    (f"generic_raw_{size}", "raw", b"".join(generic)[-size:]),
                    (f"ranked_raw_{size}", "raw", ranked[-size:]),
                    (f"tokens_raw_{size}", "raw", tokens[-size:]),
                    (f"templates_raw_{size}", "raw", b"".join(unique)[-size:]),
                    (f"trained_unique_{size}", "trained", (size, unique)),
                    (f"trained_weighted_{size}", "trained", (size, weighted)),
                ]
            )
        if args.finalize:
            candidates = [candidate for candidate in candidates if candidate[0] == "templates_raw_16384"]
        for label, fmt, corpus in candidates:
            try:
                dictionary = (
                    zstd.ZstdCompressionDict(corpus, dict_type=zstd.DICT_TYPE_RAWCONTENT)
                    if fmt == "raw"
                    else zstd.train_dictionary(corpus[0], corpus[1], level=3)
                    if fmt == "trained"
                    else None
                )
            except zstd.ZstdError as exc:
                info["variants"][label] = {"training_error": str(exc)}
                continue
            compressor = zstd.ZstdCompressor(level=3, dict_data=dictionary)
            decoder = zstd.ZstdDecompressor(dict_data=dictionary)
            started = time.perf_counter()
            packed = [compressor.compress(raw) for raw in evaluation]
            compress_ms = (time.perf_counter() - started) * 1000
            assert all(decoder.decompress(encoded) == raw for encoded, raw in zip(packed, evaluation))
            times = []
            for _ in range(5):
                started = time.perf_counter()
                [decoder.decompress(encoded) for encoded in packed]
                times.append((time.perf_counter() - started) * 1000)
            sizes = {}
            for minimum, maximum, name in [(0, 1024, "small"), (1024, 16384, "medium"), (16384, 1000000000, "large")]:
                indexes = [i for i, raw in enumerate(evaluation) if minimum <= len(raw) < maximum]
                sizes[name] = {
                    "rows": len(indexes),
                    "raw_bytes": sum(len(evaluation[i]) for i in indexes),
                    "compressed_bytes": sum(len(packed[i]) for i in indexes),
                }
            entry = {
                "compressed_bytes": sum(map(len, packed)),
                "compress_ms": round(compress_ms, 3),
                "decode_median_ms": round(statistics.median(times), 3),
                "sizes": sizes,
                "dictionary_bytes": len(dictionary.as_bytes()) if dictionary else 0,
                "format": fmt,
                "sha256": hashlib.sha256(dictionary.as_bytes()).hexdigest() if dictionary else None,
            }
            info["variants"][label] = entry
            if dictionary:
                (args.output / f"{provider}_{label}.dict").write_bytes(dictionary.as_bytes())
            print(provider, label, entry["compressed_bytes"], flush=True)
        if args.finalize:
            label = "templates_raw_16384"
            entry = info["variants"][label]
            release_entries.append({
                "provider": provider, "version": 1, "format": "raw", "file": f"{provider}_v1.dict",
                "sha256": entry["sha256"], "byte_length": entry["dictionary_bytes"],
                "policy_sha256": results["policy_sha256"], "seed": args.seed,
            })
        results["providers"][provider] = info
    if args.finalize:
        if {entry["provider"] for entry in release_entries} != {"claude_code", "codex"}:
            raise ValueError("Both provider dictionaries are required for release")
        args.finalize.mkdir(parents=True, exist_ok=False)
        for entry in release_entries:
            payload = (args.output / f"{entry['provider']}_templates_raw_16384.dict").read_bytes()
            if not 0 < len(payload) <= 16384:
                raise ValueError("Release dictionary size is invalid")
            (args.finalize / entry["file"]).write_bytes(payload)
        (args.finalize / "manifest.json").write_bytes(
            orjson.dumps({"schema_version": 1, "dictionaries": release_entries}, option=orjson.OPT_INDENT_2)
        )
    (args.output / "results.json").write_bytes(orjson.dumps(results, option=orjson.OPT_INDENT_2))
    (args.output / "public-source-allowlist.json").write_bytes(orjson.dumps(policy, option=orjson.OPT_INDENT_2))


if __name__ == "__main__":
    main()
