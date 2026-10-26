"""Ingestion fills a Codex generated image kept as a file reference (no inline ``result``)."""

import base64
import copy
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc.providers.codex import generated_images
from twicc.providers.codex.canonical import image_generation
from twicc.providers.codex.compute import CodexSessionCompute

SID = "01a1773b-6d8a-74e0-95ad-60a696415085"
PNG = generated_images.PNG_SIGNATURE + b"fake-png-body"
PNG_B64 = base64.b64encode(PNG).decode()


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    directory = home / "generated_images" / SID
    directory.mkdir(parents=True)
    monkeypatch.setattr(generated_images, "codex_home", lambda: SimpleNamespace(path=home))
    return directory


def extension_record(saved_path, **item_fields):
    item = {
        "type": "Extension", "kind": "image_gen.generation", "id": "exec-1", "status": "completed",
        "revisedPrompt": "A blue square", "transparentBackground": False, "failure": None,
        "savedPath": str(saved_path),
    }
    item.update(item_fields)
    return {
        "timestamp": "2026-10-26T08:02:46.899Z", "ordinal": 22, "type": "event_msg",
        "payload": {"type": "item_completed", "thread_id": SID, "turn_id": "t1", "item": item},
    }


def native_record(saved_path, **item_fields):
    item = {
        "type": "ImageGeneration", "id": "ig-1", "status": "completed",
        "revised_prompt": "A blue square", "saved_path": str(saved_path),
    }
    item.update(item_fields)
    return {
        "timestamp": "2026-10-26T08:02:46.899Z", "type": "event_msg",
        "payload": {"type": "item_completed", "thread_id": SID, "turn_id": "t1", "item": item},
    }


def transform(record):
    compute = CodexSessionCompute()
    compute.begin_session_compute(SID)
    return compute.transform_inline(record, session_id=SID, line_num=5)


def result_of(record):
    return record["payload"]["item"].get("result")


def saved_png(images_dir, name="a.png", data=PNG):
    path = images_dir / name
    path.write_bytes(data)
    return path


def test_inline_item_untouched(images_dir):
    path = saved_png(images_dir)
    record = extension_record(path, result="ORIGINAL")
    assert transform(record) is None
    assert result_of(record) == "ORIGINAL"


def test_file_id_only_is_filled(images_dir):
    record = extension_record(saved_png(images_dir), fileId="file-1")
    content = transform(record)
    assert result_of(record) == PNG_B64
    assert orjson.loads(content) == record
    assert record["payload"]["item"]["fileId"] == "file-1"
    assert image_generation(record).file_id == "file-1"


def test_file_id_with_empty_result_is_filled(images_dir):
    record = extension_record(saved_png(images_dir), fileId="file-1", result="")
    assert transform(record) is not None
    assert result_of(record) == PNG_B64


def test_file_id_with_input_images_is_filled(images_dir):
    record = extension_record(saved_png(images_dir), fileId="file-1", inputImages=[{"fileId": "in-1"}])
    assert transform(record) is not None
    assert result_of(record) == PNG_B64
    assert record["payload"]["item"]["inputImages"] == [{"fileId": "in-1"}]


def test_native_snake_case_item_is_filled(images_dir):
    record = native_record(saved_png(images_dir))
    assert transform(record) is not None
    assert result_of(record) == PNG_B64


def test_missing_file_leaves_item_untouched(images_dir):
    record = extension_record(images_dir / "missing.png", fileId="file-1")
    before = copy.deepcopy(record)
    assert transform(record) is None
    assert record == before


def test_no_saved_path_leaves_item_untouched(images_dir):
    record = extension_record("", fileId="file-1")
    assert transform(record) is None


def test_path_outside_allowed_dir_is_refused(images_dir, tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)
    record = extension_record(outside, fileId="file-1")
    assert transform(record) is None
    assert not result_of(record)


def test_dotdot_traversal_is_refused(images_dir, tmp_path):
    (tmp_path / "secret.png").write_bytes(PNG)
    traversal = Path(str(images_dir) + "/../../../secret.png")
    record = extension_record(traversal, fileId="file-1")
    assert transform(record) is None
    assert not result_of(record)


def test_symlink_pointing_outside_is_refused(images_dir, tmp_path):
    target = tmp_path / "secret.png"
    target.write_bytes(PNG)
    link = images_dir / "link.png"
    link.symlink_to(target)
    record = extension_record(link, fileId="file-1")
    assert transform(record) is None
    assert not result_of(record)


def test_non_png_file_is_refused(images_dir):
    record = extension_record(saved_png(images_dir, "a.png", b"not a png at all"), fileId="file-1")
    assert transform(record) is None
    assert not result_of(record)


def test_oversize_file_is_refused(images_dir, monkeypatch):
    monkeypatch.setattr(generated_images, "MAX_IMAGE_BYTES", len(PNG) - 1)
    record = extension_record(saved_png(images_dir), fileId="file-1")
    assert transform(record) is None
    assert not result_of(record)


def test_second_pass_changes_nothing(images_dir):
    record = extension_record(saved_png(images_dir), fileId="file-1")
    assert transform(record) is not None
    snapshot = copy.deepcopy(record)
    assert transform(record) is None
    assert record == snapshot


def test_image_appearing_later_is_filled_on_recompute(images_dir):
    path = images_dir / "late.png"
    record = extension_record(path, fileId="file-1")
    assert transform(record) is None
    path.write_bytes(PNG)
    assert transform(record) is not None
    assert result_of(record) == PNG_B64
