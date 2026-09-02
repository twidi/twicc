"""Register the TwiCC marketplace and (re)install the TwiCC plugin in Codex.

Codex requires marketplaces and plugin enable-state to live in the user
config (``<codex home>/config.toml``, the app-server receives this instance's
``CODEX_HOME``): CLI overrides are explicitly ignored for
these two sections (see ``core-plugins/src/manager.rs:1979`` and
``installed_marketplaces.rs:21-23``). We therefore drive the official
JSON-RPC ``marketplace/add`` + ``plugin/install`` methods at TwiCC startup,
which persist the config and materialise the plugin into
``$CODEX_HOME/plugins/cache/twicc/twicc/<version>/``.

``marketplace/add`` is idempotent only while the registered ``source``
path is unchanged: it reports ``alreadyAdded`` and leaves the persisted
``[marketplaces.twicc]`` section alone. But TwiCC's plugin lives under a
*volatile* path — the uv archive cache in UVX mode, the editable checkout
in UV mode — which differs across run modes and even between two UVX
versions (each version gets a fresh archive hash). When the source path
differs from the one already registered, Codex rejects the add with
``InvalidRequestError`` (-32600: "marketplace 'twicc' is already added from
a different source"). We therefore catch that error, ``marketplace/remove``
the stale registration, and re-add from the current source.

``plugin/install`` re-materialises the cache. For non-curated marketplaces
the cache is keyed by the plugin's ``version`` field, so bumping
``plugin.json``'s version is what forces stale skill names / contents to
refresh.

Failures are logged but never raised — TwiCC must keep starting even when
Codex is misconfigured or unreachable. A failure during the remove/re-add
recovery (or the re-add itself) aborts plugin setup: skills are
unavailable, but startup proceeds.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Manifest sub-paths Codex looks for when validating a marketplace root,
# matching ``core-plugins/src/marketplace.rs:20-23``.
MARKETPLACE_MANIFEST_CANDIDATES = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
)


async def ensure_twicc_plugin_installed() -> None:
    """Register the TwiCC marketplace and install the TwiCC plugin.

    Spawns a short-lived ``codex app-server`` client to drive the
    ``marketplace/add`` + ``plugin/install`` JSON-RPC dance, then closes.
    """
    from django.conf import settings

    if not settings.CODEX_PLUGIN_INSTALL_ENABLED:
        logger.info(
            "Codex plugin install skipped (TWICC_NO_CODEX_PLUGIN is set)"
        )
        return

    from openai_codex.async_client import AsyncCodexClient
    from openai_codex.errors import InvalidRequestError
    from openai_codex.generated.v2_all import (
        MarketplaceAddResponse,
        MarketplaceRemoveResponse,
        PluginInstallResponse,
    )

    from twicc.agent.plugin import (
        MARKETPLACE_NAME,
        PLUGIN_NAME,
        get_marketplace_dir,
    )
    from twicc.providers.codex.bin import make_codex_config

    marketplace_dir = get_marketplace_dir().resolve()

    # ``cwd`` here is the app-server's own working directory — it has no
    # bearing on plugin install, which writes to ``$CODEX_HOME``.
    config = await make_codex_config(cwd=str(Path.home()))

    try:
        async with AsyncCodexClient(config=config) as client:
            await client.initialize()

            # 1. Register the marketplace in ``<codex home>/config.toml``.
            #
            # ``marketplace/add`` only stays idempotent while the registered
            # ``source`` path is unchanged. TwiCC's plugin lives under a
            # volatile path (uv archive cache in UVX, editable checkout in
            # UV) that differs across run modes and between versions, so a
            # changed source makes Codex reject the add with
            # ``InvalidRequestError`` (-32600). Recover by removing the stale
            # registration and re-adding from the current source. We catch
            # only that error — any other failure propagates straight to the
            # outer handler. A failure of the remove or the re-add likewise
            # propagates and aborts plugin setup.
            try:
                add_resp = await client.request(
                    "marketplace/add",
                    {"source": str(marketplace_dir)},
                    response_model=MarketplaceAddResponse,
                )
            except InvalidRequestError:
                logger.info(
                    "TwiCC marketplace registered from a different source; "
                    "removing it and re-adding from %s",
                    marketplace_dir,
                )
                await client.request(
                    "marketplace/remove",
                    {"marketplaceName": MARKETPLACE_NAME},
                    response_model=MarketplaceRemoveResponse,
                )
                add_resp = await client.request(
                    "marketplace/add",
                    {"source": str(marketplace_dir)},
                    response_model=MarketplaceAddResponse,
                )

            if add_resp.already_added:
                logger.debug(
                    "TwiCC marketplace already registered (name=%s)",
                    add_resp.marketplace_name,
                )
            else:
                logger.info(
                    "Registered TwiCC marketplace (name=%s, root=%s)",
                    add_resp.marketplace_name,
                    add_resp.installed_root,
                )

            # 2. Find the marketplace manifest under ``installed_root`` —
            # ``plugin/install`` expects the path to the manifest file, not
            # the marketplace root.
            installed_root = Path(add_resp.installed_root.root)
            manifest = _find_marketplace_manifest(installed_root)
            if manifest is None:
                logger.error(
                    "TwiCC marketplace manifest not found under %s (looked for %s)",
                    installed_root,
                    ", ".join(MARKETPLACE_MANIFEST_CANDIDATES),
                )
                return

            # 3. Install the plugin: materialises the cache to the current
            # ``version`` in ``plugin.json`` and writes
            # ``[plugins."twicc@twicc"] enabled = true`` in the user config.
            await client.request(
                "plugin/install",
                {
                    "pluginName": PLUGIN_NAME,
                    "marketplacePath": str(manifest),
                },
                response_model=PluginInstallResponse,
            )
            logger.info(
                "Installed TwiCC plugin (plugin=%s, marketplace=%s)",
                PLUGIN_NAME,
                MARKETPLACE_NAME,
            )
    except Exception:
        logger.exception(
            "Failed to ensure TwiCC Codex plugin is installed — skills will be unavailable"
        )


def _find_marketplace_manifest(root: Path) -> Path | None:
    """Resolve the marketplace manifest path under ``root``, if any."""
    for candidate in MARKETPLACE_MANIFEST_CANDIDATES:
        path = root / candidate
        if path.is_file():
            return path
    return None
