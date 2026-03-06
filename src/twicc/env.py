"""
Environment variable utilities.
"""

_CLAUDE_CODE_PREFIXES = ("CLAUDE_CODE", "CLAUDECODE")


def purge_claude_code_vars(env: dict) -> None:
    """Remove Claude Code environment variables from *env* in-place.

    Claude Code sets variables like ``CLAUDE_CODE_ENTRYPOINT`` or
    ``CLAUDECODE_*`` that, when inherited by child processes, cause new
    Claude Code instances to think they are already running inside an SDK
    session.  This helper strips them so spawned processes start clean.
    """
    for key in list(env):
        if key.startswith(_CLAUDE_CODE_PREFIXES):
            del env[key]
