import os
import stat
import sys

import pytest

from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation, hermetic_cwd

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership checks")


@pytest.fixture
def hermetic_logs():
    """Collect the warning lines of ``twicc.providers.hermetic``.

    Owns the logger instead of using ``caplog``: the test settings disable existing loggers, which makes
    ``caplog`` depend on the import order of the suite (same approach as the ``rejections`` fixture of
    ``tests/test_title_output_validation.py``). Copy this fixture into every test module that counts log lines.
    """
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_errors_carry_a_reason_and_log_one_warning(hermetic_logs):
    err = HermeticConfigError("cwd", "boom")
    violation = HermeticGuardViolation("tools not empty")
    assert err.reason == "cwd" and str(err) == "boom"
    assert violation.reason == "tools not empty"
    assert hermetic_logs == ["hermetic call failed: cwd (boom)", "hermetic call failed: guard (tools not empty)"]


@posix_only
def test_creates_the_directory_with_mode_0700(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    assert path == (tmp_path / f"hermetic-llm-{os.getuid()}").resolve()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


@posix_only
def test_second_call_is_a_no_op(tmp_path):
    assert hermetic_cwd(base=tmp_path) == hermetic_cwd(base=tmp_path)


@posix_only
def test_non_empty_directory_is_refused_and_names_the_entry(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    (path / "AGENTS.md").write_text("stray")
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd"
    assert "AGENTS.md" in str(info.value) and "remove" in str(info.value).lower()
    assert (path / "AGENTS.md").exists()  # never purged


@posix_only
def test_wrong_mode_is_refused(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    path.chmod(0o755)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "0700" in str(info.value)


@posix_only
def test_symlink_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / f"hermetic-llm-{os.getuid()}").symlink_to(target)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "symlink" in str(info.value)


@posix_only
def test_foreign_owner_is_refused(tmp_path, monkeypatch):
    path = hermetic_cwd(base=tmp_path)
    real_getuid = os.getuid
    monkeypatch.setattr(os, "getuid", lambda: real_getuid() + 1)  # the directory now looks foreign
    # Name is built from the uid, so recreate the expectation with the real name:
    monkeypatch.setattr("twicc.providers.hermetic._dir_name", lambda: path.name)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "TMPDIR" in str(info.value)


def test_path_containing_the_product_name_is_refused(tmp_path):
    base = tmp_path / "TwiCC-tmp"
    base.mkdir()
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=base)
    assert info.value.reason == "cwd" and "twicc" in str(info.value).lower()


def test_default_base_is_the_system_temporary_directory(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert hermetic_cwd().parent == tmp_path.resolve()
