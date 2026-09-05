"""Live external MCP configuration and exclusive route classification."""

from twicc.core.services.public_origin import normalize_public_origin
from twicc.core.services.origin_policy import recognize_authority
from twicc.synced_settings import read_routing_settings

RESOURCE_METADATA = "/.well-known/oauth-protected-resource/mcp"
SERVER_METADATA = "/.well-known/oauth-authorization-server/mcp/oauth"


def base_url():
    from twicc.mcp import mcp_enabled

    if not mcp_enabled():
        return ""
    snapshot = read_routing_settings()
    if not snapshot.available or snapshot.settings.get("externalMcpEnabled") is not True:
        return ""
    result = normalize_public_origin(snapshot.settings.get("mcpBaseUrl", ""))
    if not result.value or result.scheme != "https" or result.hostname in ("localhost", "127.0.0.1", "::1"):
        return ""
    for key in ("publicBaseUrl", "shareBaseUrl", "peerBaseUrl"):
        other = recognize_authority(snapshot.settings.get(key, ""))
        if other and other.hostname == result.hostname:
            return ""
    return result.value


def resource_url():
    return base_url() + "/mcp"


def issuer_url():
    return base_url() + "/mcp/oauth"


def protocol_path(path):
    return path in ("/mcp", "/mcp/", RESOURCE_METADATA, SERVER_METADATA) or path.startswith("/mcp/oauth/")
