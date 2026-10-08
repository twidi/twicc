"""Shared publication grammar and source-order catalog contracts."""

from pathlib import Path

import orjson
import pytest

from twicc.inline_artifacts.publications import latest_publications, merge_publications, parse_inline_artifact_blocks


CASES = orjson.loads((Path(__file__).parents[1] / "fixtures/inline-artifacts/publications.json").read_bytes())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_publication_grammar(case):
    assert [block._asdict() for block in parse_inline_artifact_blocks(case["text"])] == case["expected"]


def publication(line_num, text_block_index=0, tag_offset=0, artifact_id="preferences"):
    return {
        "artifact_id": artifact_id,
        "line_num": line_num,
        "text_block_index": text_block_index,
        "tag_offset": tag_offset,
        "src": f"inline-artifacts/{artifact_id}/index.html",
        "title": artifact_id,
        "height": 360,
    }


def test_catalog_orders_source_occurrences_not_arrival():
    catalog = merge_publications({}, [publication(87), publication(42), publication(87)])
    assert catalog == {"schema": 1, "publications": [publication(42), publication(87)]}
    assert latest_publications(catalog) == {"preferences": publication(87)}


def test_catalog_orders_text_blocks_and_offsets():
    records = [publication(42, 1, 0), publication(42, 0, 30), publication(42, 0, 5)]
    catalog = merge_publications({}, records)
    assert catalog["publications"] == [records[2], records[1], records[0]]
    assert latest_publications(catalog)["preferences"] == records[0]


def test_catalog_merges_without_mutating_input():
    existing = {"schema": 1, "publications": [publication(87)]}
    incoming = publication(42, artifact_id="calculator")
    merged = merge_publications(existing, [incoming])
    assert existing == {"schema": 1, "publications": [publication(87)]}
    assert latest_publications(merged) == {"calculator": incoming, "preferences": publication(87)}
    merged["publications"][0]["title"] = "Changed"
    assert incoming["title"] == "calculator"


def test_latest_handles_empty_and_unsorted_catalogs():
    assert merge_publications({}, []) == {}
    assert latest_publications({}) == {}
    assert latest_publications({"schema": 1, "publications": [publication(87), publication(42)]}) == {
        "preferences": publication(87),
    }
