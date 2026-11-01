"""Inactive Claude transcript mutation, owned by the publication worker."""
import os
import subprocess
import sys

from .title_control import canonicalize_claude_title


# Keep SDK append/UUID semantics while isolating the runtime provider home.
_RENAME_SCRIPT = 'from claude_agent_sdk import rename_session; import sys; rename_session(sys.argv[1], sys.argv[2])'


def rename_session_in_jsonl(session_id: str, title: str) -> None:
    """Run the SDK file helper with the runtime provider's environment.

    The caller holds the process reservation and verifies process absence.
    This launches Python only. It does not launch Claude or change the parent
    process environment. Failures propagate to token-checked abandonment.
    """
    from twicc.provider_homes import provider_env_overlay

    validation = canonicalize_claude_title(title)
    if validation.error:
        raise ValueError(validation.error)
    subprocess.run(
        [sys.executable, '-c', _RENAME_SCRIPT, session_id, validation.title],
        env={**os.environ, **provider_env_overlay()},
        timeout=10, check=True, capture_output=True,
    )
