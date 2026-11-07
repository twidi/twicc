"""Git failure messages keep the detail out of the catalog separator."""

from twicc.git import _git_failed_message


def test_failure_without_detail_uses_the_plain_key():
    message = _git_failed_message("Git log failed", "backend.git.git_log_failed", "")
    assert str(message) == "Git log failed"
    assert message.ui_message == {"key": "backend.git.git_log_failed", "params": {}}


def test_failure_with_detail_uses_the_detail_key_and_the_raw_detail():
    message = _git_failed_message("Git log failed", "backend.git.git_log_failed", "fatal: bad revision")
    assert str(message) == "Git log failed: fatal: bad revision"
    assert message.ui_message == {
        "key": "backend.git.git_log_failed_detail",
        "params": {"detail": "fatal: bad revision"},
    }
