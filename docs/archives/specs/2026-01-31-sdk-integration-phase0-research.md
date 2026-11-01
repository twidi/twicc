# SDK Integration - Phase 0: Research (Draft)

**Date:** 2026-01-31
**Status:** Exploration terminée
**Author:** Brainstorming session

## Overview

This document contains the exploratory analysis of the Claude Agent SDK (Python) for integration into TwiCC. It covers the SDK's architecture, API, message types, and comparison with the JavaScript SDK.

This analysis serves as reference documentation for Phase 1 (implementation) and Phase 2 (extended features).

---

## 1. Package Information

- **Package name:** `claude-agent-sdk` (not `claude-code-sdk`)
- **GitHub:** https://github.com/anthropics/claude-agent-sdk-python
- **Version analyzed:** 0.1.26
- **Python requirement:** 3.10+

---

## 2. Architecture

The SDK works by spawning the Claude Code CLI as a subprocess and communicating via stdin/stdout with JSON messages.

```
Python Application
    ↓
claude-agent-sdk (query() or ClaudeSDKClient)
    ↓
Subprocess: `claude` CLI
    ↓ stdin/stdout JSON stream
Claude Code CLI
    ↓
Claude API
```

---

## 3. Two Modes of Operation

### One-shot mode: `query()`
```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for msg in query(prompt="Hello", options=ClaudeAgentOptions(...)):
    print(msg)
```

### Conversational mode: `ClaudeSDKClient`
```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async with ClaudeSDKClient(options=ClaudeAgentOptions(...)) as client:
    await client.query("First message")
    async for msg in client.receive_response():
        handle(msg)

    await client.query("Follow-up")
    async for msg in client.receive_response():
        handle(msg)
```

---

## 4. ClaudeAgentOptions - All Parameters

```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    # === Model ===
    model="claude-opus-4-5",
    fallback_model="claude-sonnet-4-5",

    # === Working directory ===
    cwd="/path/to/project",
    add_dirs=["/extra/dir1", "/extra/dir2"],

    # === Permissions ===
    permission_mode="default",  # "default" | "acceptEdits" | "plan" | "bypassPermissions"
    can_use_tool=my_callback,   # Custom permission callback

    # === Tools ===
    allowed_tools=["Bash", "Read", "Write", "Edit"],
    disallowed_tools=["WebFetch"],

    # === Session ===
    resume="session-id-to-resume",  # Resume existing session
    continue_conversation=True,
    fork_session=False,
    max_turns=50,
    max_budget_usd=5.0,

    # === MCP Servers ===
    mcp_servers={...},

    # === Hooks ===
    hooks={
        "PreToolUse": [...],
        "PostToolUse": [...],
    },

    # === Extra CLI arguments ===
    extra_args={
        "chrome": None,  # Enables Chrome MCP (--chrome)
    },

    # === Streaming ===
    include_partial_messages=True,  # Receive StreamEvent

    # === System prompt ===
    system_prompt="You are a helpful assistant",

    # === Sandbox ===
    sandbox={
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
    },
)
```

---

## 5. Message Types Received from SDK

### SystemMessage (with subtype="init")
First message received, contains the session_id:
```python
@dataclass
class SystemMessage:
    subtype: str      # "init" for initialization
    data: dict        # Contains session_id and other metadata
```

**Critical:** The `session_id` is obtained from `msg.data["session_id"]` when `msg.subtype == "init"`.

### AssistantMessage
```python
@dataclass
class AssistantMessage:
    content: list[ContentBlock]  # TextBlock, ToolUseBlock, ThinkingBlock, ToolResultBlock
    model: str
    parent_tool_use_id: str | None
    error: str | None
```

### ResultMessage
Marks the end of a response turn:
```python
@dataclass
class ResultMessage:
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str          # Also contains session_id
    total_cost_usd: float | None
    usage: dict | None
```

### UserMessage
Echo of user messages (useful for replay):
```python
@dataclass
class UserMessage:
    content: str | list[ContentBlock]
    uuid: str | None
    parent_tool_use_id: str | None
```

### StreamEvent (when include_partial_messages=True)
```python
@dataclass
class StreamEvent:
    uuid: str
    session_id: str
    event: dict  # Raw Anthropic API stream event
```

---

## 6. Content Blocks

```python
@dataclass
class TextBlock:
    text: str

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | list | None
    is_error: bool | None
```

---

## 7. Permission Modes

| Mode | Behavior |
|------|----------|
| `"default"` | Ask for confirmation on dangerous tools |
| `"acceptEdits"` | Auto-accept file edits |
| `"plan"` | Planning mode, no execution |
| `"bypassPermissions"` | Allow everything (dangerous) |

---

## 8. Custom Permission Callback

```python
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

async def can_use_tool(
    tool_name: str,
    tool_input: dict,
    context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:

    if tool_name == "Bash" and "rm -rf" in tool_input.get("command", ""):
        return PermissionResultDeny(
            message="Dangerous command refused",
            interrupt=True
        )

    return PermissionResultAllow()
```

**Important:** Using `can_use_tool` requires streaming mode (AsyncIterable prompt).

---

## 9. Image and Document Upload

Images and documents are sent as base64 in the message content:

```python
import base64

async def send_with_image():
    with open("image.png", "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    yield {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this image"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64
                    }
                }
            ]
        },
        "parent_tool_use_id": None,
        "session_id": "my-session"
    }
```

**Supported image types:** `image/png`, `image/jpeg`, `image/gif`, `image/webp`

**Supported document types:**
- `application/pdf` (base64)
- `text/plain` (raw text)

---

## 10. Comparison with JavaScript SDK

The Python SDK (`claude-agent-sdk`) is functionally equivalent to the JavaScript SDK (`@anthropic-ai/claude-agent-sdk`). Both use the same protocol.

| Feature | Python SDK | JS SDK |
|---------|------------|--------|
| query() one-shot | ✅ | ✅ |
| Conversational client | ✅ ClaudeSDKClient | ✅ AgentSdk |
| Streaming | ✅ AsyncIterable | ✅ AsyncGenerator |
| permission_mode | ✅ | ✅ |
| can_use_tool callback | ✅ | ✅ |
| Hooks | ✅ | ✅ |
| MCP servers | ✅ | ✅ |
| Resume session | ✅ | ✅ |
| Images/documents | ✅ base64 | ✅ base64 |
| Extra CLI args | ✅ extra_args | ✅ extraArgs |

**Minor differences:**
- Python uses `anyio` for async (works with asyncio and trio)
- Python uses dataclasses, JS uses TypeScript interfaces
- Python escapes keywords: `async_`, `continue_` (auto-converted for CLI)
- Python doesn't support SessionStart/SessionEnd/Notification hooks

---

## Appendix: SDK Installation

```bash
uv add claude-agent-sdk
```

Verify installation:
```python
import claude_agent_sdk
print(claude_agent_sdk.__version__)  # Should print 0.1.26 or higher
```
