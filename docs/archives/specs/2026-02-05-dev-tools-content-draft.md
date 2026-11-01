# Dev Tools Panel - Content Planning (Draft)

**Date:** 2026-02-05
**Status:** DRAFT - Research notes and decisions from conversation. Not a ready-to-implement spec.

## Context

This document captures all research and decisions made about what will go INSIDE the Dev Tools Panel (defined in `2026-02-05-dev-tools-panel.md`). The panel itself is just a skeleton; this doc covers the future content: Terminal, File Explorer, Code Editor, Git tools.

The goal is to have a complete dev environment accessible from the browser, especially useful when accessing twicc remotely (phone, someone else's computer, via tunnel).

## Architecture Decision: No Theia

### What was considered

**Theia Platform** (https://theia-ide.org/) was initially considered as an all-in-one solution. Theia is the open-source framework behind VS Code-like IDEs (used by Eclipse Che, Gitpod, Arduino IDE).

Two approaches were evaluated:
- **Theia IDE**: Full application, too heavy
- **Theia Platform**: Modular, pick-and-choose components via npm packages (`@theia/core`, `@theia/filesystem`, `@theia/editor`, `@theia/terminal`, `@theia/git`, `@theia/scm`, `@theia/navigator`, `@theia/monaco`, etc.)

### Why Theia was rejected

1. **Requires a dedicated Node.js backend**: Theia's architecture is a two-process model (Node.js backend + browser frontend) communicating via JSON-RPC over WebSocket. Our backend is Django/Python.
2. **Sidecar complexity**: Running Theia as a separate Node.js process alongside Django introduces:
   - A second port to manage
   - CORS issues when accessed via tunnel
   - Cloudflare Tunnel only supports standard ports (443/80), so path-based routing or multiple subdomains would be needed
3. **Multi-workspace limitation**: One Theia backend instance = one user/workspace context. No native multi-tenancy. To have multiple sessions open on different directories simultaneously, you'd need multiple Theia instances or a custom extension.

### Decision: Individual components, no dedicated Node.js backend

Use standalone JS libraries for each feature, with Django as the backend for all server-side operations (filesystem access, PTY, git commands). This keeps the single-backend architecture.

## Components Selected

### 1. Terminal

**Frontend:** `@xterm/xterm` + `@xterm/addon-fit`

Xterm.js is THE standard web terminal emulator. Framework-agnostic, works with any backend.

**Backend:** Django Channels (already in the project) + Python `pty` module

The architecture is straightforward:
```
User types -> xterm.js -> WebSocket (Django Channels) -> Python pty -> bash
                                                                    |
User sees  <- xterm.js <- WebSocket (Django Channels) <- Python pty <- bash output
```

**Reference implementation:** The project `claude-code-viewer` (`/home/twidi/dev/claude-code-viewer`) uses this exact stack but with Node.js:
- Frontend: `@xterm/xterm` v6.0.0 + `@xterm/addon-fit` v0.11.0
- Backend: `@homebridge/node-pty-prebuilt-multiarch` v0.13.1 + Hono WebSocket
- Singleton PTY pattern (one terminal per app, survives navigation)
- WebSocket endpoint at `/api/ws/terminal`
- REST endpoint `/api/terminal/resize` for resize operations
- Features: auto-reconnect, mobile touch support (scroll/select modes), copy selection, "add to chat"

**Python equivalents:**
| Node.js | Python |
|---------|--------|
| `node-pty` | `pty` (stdlib) or `ptyprocess` |
| Hono WebSocket | Django Channels (already used) |

**Reference Python projects:**
- [pyxtermjs](https://github.com/cs01/pyxtermjs) - Flask + flask-socketio + xterm.js
- [django-xtermjs](https://github.com/MahmoudAlyy/django-xtermjs) - Django + python-socketio + xterm.js

### 2. Code Editor

**Library:** Monaco Editor

Options for Vue 3 integration:
- `@guolao/vue-monaco-editor` (Vue wrapper)
- `monaco-editor-vue3`
- Or direct integration with `monaco-editor` npm package

Monaco also provides a built-in diff editor (`MonacoDiffEditor`) which could complement the git diff viewer.

### 3. File Tree / Explorer

**Research results - Vue 3 file tree components:**

| Component | Standalone | Lazy Load | Drag & Drop | Maintained |
|-----------|-----------|-----------|-------------|------------|
| **@grapoza/vue-tree** | Yes | Yes | Yes | Yes (v7.0.4, 2 months ago) |
| **Reka UI Tree** | Yes (headless) | DIY | DIY | Yes |
| PrimeVue Tree | No (full lib) | Yes | Yes | Yes |
| Element Plus Tree | No (full lib) | Yes | Yes | Yes |
| Naive UI Tree | No (full lib) | Yes | Limited | Yes |
| vue3-treeview | Yes | Yes | Yes | Stale (2 years) |

**Recommended: `@grapoza/vue-tree`**
- Standalone (no UI framework dependency)
- Actively maintained
- Async loading for API integration
- Drag & drop support
- ARIA compliant accessibility
- Package: `@grapoza/vue-tree` on npm

**Alternative: Reka UI Tree** (headless, if we want full control over styling)
- True headless component (was Radix Vue, now Reka UI)
- `TreeRoot`, `TreeItem`, `TreeVirtualizer` components
- Full keyboard navigation, ARIA compliant
- We'd build our own styling

**Backend needed:** Django API endpoints for:
- List directory contents (with lazy loading)
- Read file content
- Write file content

### 4. Git Tools

#### 4a. Diff Viewer

**Recommended: `@git-diff-view/vue`**
- Native Vue 3 support (also React, Svelte, Solid)
- Split and unified views
- Full syntax highlighting (HAST AST-based)
- Virtual scrolling for performance
- Web Worker support
- Light/dark themes
- SSR-ready
- Package: `@git-diff-view/vue` on npm
- Demo: https://mrwangjusttodo.github.io/git-diff-view/

**Alternatives:**
| Library | Vue 3 native | Notes |
|---------|-------------|-------|
| `v-code-diff` | Yes | Good for simple diffs |
| `diff2html` | Wrapper needed | Framework-agnostic, GitHub-style |
| `vue-diff` (hoiheart) | Yes | ARCHIVED, last release June 2022 |
| Monaco Diff Editor | Wrapper needed | If already using Monaco for editor |

#### 4b. Branch/Commit Graph Visualization

**Recommended: `@gitgraph/js`** (even though archived)

GitGraph.js produces beautiful branch visualizations. The project is archived (July 2024, unmaintained since 2019) but the code is stable and works well. For read-only graph rendering, this is fine.

- Package: `@gitgraph/js` (vanilla JS, wrap in Vue component)
- Also exists: `@gitgraph/react` (React-only)
- Templates: "metro", "blackarrow", custom
- Events: onMessageClick, onMouseOver, etc.

**Why use it despite being archived:**
- It's rendering code, not security-critical
- Git APIs haven't changed
- Read-only usage
- No equivalent with the same visual quality

**Alternatives considered:**
| Library | Status | Notes |
|---------|--------|-------|
| Mermaid.js gitgraph | Active (67k+ stars) | Declarative only, not for real git data |
| commit-graph (CommitGraph) | Active | React-only, designed for real git data, used by DoltHub |
| @tomplum/react-git-log | Active | React-only |
| Cytoscape.js | Active | General graph lib, would need custom dev |

**Backend needed:** Django API endpoints for:
- `git log` (commit history with branch info)
- `git diff` (for diff viewer)
- `git status`
- `git branch` (list branches)

## Panel Layout (Future)

The Dev Tools Panel will have internal tabs:

```
[Terminal] [Files] [Git]
```

Each tab's content:

### Terminal tab
- Full xterm.js terminal
- Connected to the session's working directory
- Singleton pattern (survives tab switches)

### Files tab
- Split view: File tree on left, Editor on right
- File tree shows the session's working directory
- Clicking a file opens it in Monaco Editor
- Read-only or editable (TBD)

### Git tab
- Sub-sections or sub-tabs:
  - Status (modified files, staged, etc.)
  - Diff viewer (select file to see diff)
  - Graph (branch visualization)

## Per-Session vs Global

Important architectural question to resolve:

- **Terminal**: Could be per-session (different cwd) or global. `claude-code-viewer` uses a global singleton. For twicc, per-session (one terminal per session, opened on that session's cwd) might make more sense.
- **Files**: Per-session (shows that session's project directory)
- **Git**: Per-session (shows that session's git repo state)

This means the panel content changes when switching sessions (but the panel skeleton stays the same).

## Remote Access Considerations

### Local usage
- Two ports: Django on 3500, no extra port needed (everything goes through Django)
- Terminal PTY via Django Channels WebSocket
- File/Git operations via Django REST API

### Tunnel usage
- Only Django port needs to be tunneled
- All terminal, file, and git operations go through Django
- No CORS issues since everything is same-origin
- Cloudflare Tunnel (or any tunnel) only needs to expose one port

This is a major advantage of the "individual components + Django backend" approach over Theia sidecar.

## Summary of npm packages to install (when implementing)

```json
{
  "dependencies": {
    "@xterm/xterm": "^6.0.0",
    "@xterm/addon-fit": "^0.11.0",
    "monaco-editor": "latest",
    "@grapoza/vue-tree": "latest",
    "@git-diff-view/vue": "latest",
    "@gitgraph/js": "latest"
  }
}
```

## Summary of Django backend work needed

1. **WebSocket Consumer for Terminal PTY** (Django Channels)
   - Spawn PTY process (Python `pty` module)
   - Bridge PTY I/O to WebSocket
   - Handle resize messages

2. **REST API endpoints for filesystem**
   - `GET /api/fs/list/?path=...` - List directory contents
   - `GET /api/fs/read/?path=...` - Read file content
   - `POST /api/fs/write/` - Write file content

3. **REST API endpoints for Git**
   - `GET /api/git/status/?path=...` - Git status
   - `GET /api/git/log/?path=...` - Commit history
   - `GET /api/git/diff/?path=...` - Diff output
   - `GET /api/git/branches/?path=...` - Branch list
