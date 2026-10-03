"""Shared building blocks for CLI commands that talk to the live TwiCC server
via the ``drop-requests/`` drop-file protocol (``twicc create-session``,
``twicc send-message``, ``twicc send-messages``, ``twicc update-session``,
``twicc update-sessions``, ``twicc sessions stop``, ...).

The modules in this sub-package are intentionally agnostic of the action
performed on the server side: they handle discovery (heartbeat check),
atomic drop-file write, status polling, output formatting, prompt resolution,
attachment validation+encoding, local bootstrap snapshot, presets/overrides
merge, validation, ``--help`` enrichment, and existing-session lookup.
Each command module composes them with its own action-specific glue.

Leading underscore in the package name signals "internal to twicc.cli".
"""
