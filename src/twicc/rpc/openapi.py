"""Generate an OpenAPI 3.1 document from the RPC route registry."""

from __future__ import annotations

from twicc.version import get_version

_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "exit_code": {"type": "integer", "description": "CLI exit code (0 = success)."},
        "result": {"description": "The command's structured result, or null."},
        "error": {"type": ["string", "null"], "description": "Failure message, or null."},
    },
    "required": ["exit_code"],
}


def build_openapi(registry) -> dict:
    paths: dict = {}
    for path, spec in sorted(registry.items()):
        paths[f"/rpc/{path}"] = {
            "post": {
                "summary": spec.summary or path,
                "operationId": "rpc_" + path.replace("/", "_").replace("-", "_"),
                "security": [{"bearerAuth": []}],
                "requestBody": {
                    "required": bool(spec.json_schema.get("required")),
                    "content": {"application/json": {"schema": spec.json_schema}},
                },
                "responses": {
                    "200": {
                        "description": "Command executed (inspect exit_code).",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RpcEnvelope"}
                            }
                        },
                    },
                    "400": {"description": "Malformed or invalid request body."},
                    "401": {"description": "Missing/invalid token (when protection is on)."},
                    "404": {"description": "Unknown command."},
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "TwiCC RPC API", "version": get_version()},
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {"RpcEnvelope": _ENVELOPE_SCHEMA},
        },
        "paths": paths,
    }
