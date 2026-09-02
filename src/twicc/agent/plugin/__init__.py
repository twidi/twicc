"""TwiCC plugin.

Provides skills and commands that enhance sessions started through TwiCC,
giving the assistant access to TwiCC-specific capabilities like session search.

Layout::

    plugin/                          <- marketplace root (Codex)
    ├── .claude-plugin/
    │   └── marketplace.json
    └── twicc/                       <- plugin root (Claude Code & Codex)
        ├── .claude-plugin/plugin.json
        └── skills/...

Claude Code consumes the plugin root directly via ``get_plugin_dir()``,
passed to ``ClaudeAgentOptions(plugins=[...])`` per session.

Codex requires marketplaces and plugin enable-state to be persisted in
``<codex home>/config.toml`` (CLI overrides are ignored for those two sections
by design). TwiCC's Codex orchestrator therefore drives the official
``marketplace/add`` + ``plugin/install`` JSON-RPC at startup — see
``twicc.providers.codex.plugin_install``.
"""

from pathlib import Path


PLUGIN_NAME = "twicc"
MARKETPLACE_NAME = "twicc"


def get_plugin_dir() -> Path:
    """Return the path to the TwiCC plugin directory.

    This is the directory that contains `.claude-plugin/plugin.json`
    and should be passed to ClaudeAgentOptions as a plugin path.
    """
    return Path(__file__).parent / PLUGIN_NAME


def get_marketplace_dir() -> Path:
    """Return the path to the TwiCC marketplace directory.

    This is the directory that contains `.claude-plugin/marketplace.json`
    and should be referenced as a Codex marketplace ``source``.
    """
    return Path(__file__).parent
