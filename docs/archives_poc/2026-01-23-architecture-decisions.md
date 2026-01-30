# Architecture Decisions — Claude Code Web UI

## Overview

A standalone, self-contained web application to replace the Claude Code CLI. Single process, zero external services, one command to launch.

```
uv run ./run.py
```

---

## Stack Summary

| Layer | Technology |
|-------|------------|
| Package Manager | uv |
| Backend | Django (ASGI) |
| Server | Uvicorn |
| WebSocket | Django Channels + InMemoryChannelLayer |
| Database | SQLite |
| File Watching | watchfiles |
| Frontend Build | Vite |
| Frontend Framework | Vue.js 3 (SFC) |
| State Management | Pinia + VueUse |
| UI Components | Web Awesome |

---

## Architecture Diagram

```
uv run ./run.py
│
├── Django ASGI (Uvicorn)
│   ├── HTTP (pages, API)
│   └── Channels WebSocket ← InMemoryChannelLayer
│
├── watchfiles (asyncio task)
│   └── JSONL file changed → parse → save to DB → signal post_save → broadcast WS
│
├── SQLite
│
└── Frontend (Vue.js)
    └── WebSocket JSON → reactive store → auto-update UI
```

---

## Backend

### Dependencies

```toml
# pyproject.toml
[project]
name = "claude-code-web"
version = "0.1.0"
dependencies = [
    "django",
    "channels",
    "uvicorn[standard]",  # ASGI server with websockets support
    "watchfiles",
]

[project.scripts]
claude-code-web = "claude_code_web:main"

[dependency-groups]
dev = [
    "ruff",
    "mypy",
    "pytest",
    "pytest-django",
]

[tool.ruff]
line-length = 120

[tool.mypy]
python_version = "3.13"
strict = true

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "claude_code_web.settings"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Entry Point

```python
#!/usr/bin/env -S uv run
# run.py

from claude_code_web import main

if __name__ == "__main__":
    main()
```

```python
# src/claude_code_web/__init__.py

def main():
    import sys
    from claude_code_web.bootstrap import bootstrap
    from claude_code_web.server import run_server
    
    bootstrap()
    run_server(port=sys.argv[1] if len(sys.argv) > 1 else "8000")
```

### Bootstrap (Auto-Setup)

```python
# src/claude_code_web/bootstrap.py

import subprocess
from pathlib import Path
import os

def bootstrap():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'claude_code_web.settings')
    
    import django
    django.setup()
    
    root = Path(__file__).resolve().parent.parent.parent
    frontend_dir = root / 'frontend'
    
    # Frontend build (skip if prebuild)
    if frontend_dir.exists():
        if needs_npm_install(frontend_dir):
            print("📦 Installing npm dependencies...")
            subprocess.run(['npm', 'install'], cwd=frontend_dir, check=True)
        
        if needs_build(frontend_dir):
            print("🔨 Building frontend...")
            subprocess.run(['npm', 'run', 'build'], cwd=frontend_dir, check=True)
    
    # Django migrations
    from django.core.management import call_command
    print("🗃️ Running migrations...")
    call_command('migrate', verbosity=0)
    
    print("📁 Collecting static files...")
    call_command('collectstatic', verbosity=0, interactive=False)
    
    print("✅ Ready!")

def needs_npm_install(frontend_dir):
    node_modules = frontend_dir / 'node_modules'
    package_json = frontend_dir / 'package.json'
    if not node_modules.exists():
        return True
    return package_json.stat().st_mtime > node_modules.stat().st_mtime

def needs_build(frontend_dir):
    dist = frontend_dir / 'dist'
    src = frontend_dir / 'src'
    if not dist.exists():
        return True
    return dir_mtime(src) > dir_mtime(dist)

def dir_mtime(path):
    return max(f.stat().st_mtime for f in path.rglob('*') if f.is_file())
```

### Django Settings (Minimal)

```python
# src/claude_code_web/settings.py

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "channels",
    "claude_code_web.core",
]

# No auth, no sessions
MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

# Channels
ASGI_APPLICATION = "claude_code_web.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
```

---

## Database Schema

### SQLite Limits (Reference)

SQLite handles our expected data volume without issues:

| Metric | Expected (×10 safety) | SQLite Limit |
|--------|----------------------|--------------|
| Number of rows | ~1.6M | Unlimited* |
| Max row size | ~111 MB | 1 GB |
| Total DB size | ~17 GB | 281 TB |

### Schema Design

Two tables: one for tracking file state (append-only sync), one for storing JSONL content.

```sql
-- Track file read state for incremental sync
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    last_offset INTEGER DEFAULT 0,  -- byte position where we stopped reading
    last_line INTEGER DEFAULT 0,    -- last line number read
    mtime REAL                      -- file modification time for change detection
);

-- Store JSONL content
CREATE TABLE lines (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    line_num INTEGER NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(path, line_num)
);

CREATE INDEX idx_lines_path ON lines(path);
```

### JSON Querying

SQLite 3.38+ has native JSON support:

```sql
-- Extract JSON fields
SELECT content ->> '$.type' FROM lines;                     -- as text
SELECT content -> '$.tags' FROM lines;                      -- as JSON
SELECT json_extract(content, '$.user.email') FROM lines;    -- function syntax

-- Filter on JSON
SELECT * FROM lines WHERE content ->> '$.type' = 'message';
SELECT * FROM lines WHERE json_extract(content, '$.count') > 100;
```

### Indexing JSON Fields

For frequently queried JSON paths:

```sql
-- Option 1: Expression index
CREATE INDEX idx_type ON lines(content ->> '$.type');

-- Option 2: Generated column (more flexible)
ALTER TABLE lines ADD COLUMN msg_type TEXT 
    GENERATED ALWAYS AS (content ->> '$.type');
CREATE INDEX idx_msg_type ON lines(msg_type);
```

### Size Considerations

- JSON is stored as TEXT (no compression, no overhead)
- `LENGTH()` counts Unicode characters, not bytes
- Use `LENGTH(CAST(content AS BLOB))` for byte count
- Expected overhead: ~10% (indexes + structure)

### Sync Strategy

Files are append-only. On startup or inotify trigger:

1. Compare `mtime` to detect changes
2. `seek()` to `last_offset`
3. Read new lines, insert into DB
4. Update `last_offset` and `last_line`

```python
def sync_file(conn: sqlite3.Connection, path: Path):
    mtime = path.stat().st_mtime
    row = conn.execute(
        "SELECT last_offset, last_line, mtime FROM files WHERE path = ?", 
        (str(path),)
    ).fetchone()
    
    # Skip if unchanged
    if row and row["mtime"] == mtime:
        return
    
    offset, line_num = (row["last_offset"], row["last_line"]) if row else (0, 0)
    
    with open(path, "r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            line_num += 1
            conn.execute(
                "INSERT OR REPLACE INTO lines (path, line_num, content) VALUES (?, ?, ?)",
                (str(path), line_num, line.rstrip("\n"))
            )
        new_offset = f.tell()
    
    conn.execute("""
        INSERT INTO files (path, last_offset, last_line, mtime) VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET last_offset = ?, last_line = ?, mtime = ?
    """, (str(path), new_offset, line_num, mtime, new_offset, line_num, mtime))
```

---

### ASGI + File Watcher

```python
# src/claude_code_web/asgi.py

import os
import asyncio
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.generic.websocket import AsyncJsonWebsocketConsumer

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'claude_code_web.settings')

django_asgi_app = get_asgi_application()

# WebSocket consumer
class UpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("updates", self.channel_name)
    
    async def send_update(self, event):
        await self.send_json(event["data"])

# Routing
from django.urls import path
websocket_urlpatterns = [
    path("ws/", UpdatesConsumer.as_asgi()),
]

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": URLRouter(websocket_urlpatterns),
})

# Start file watcher
from claude_code_web.watcher import start_watcher
asyncio.create_task(start_watcher())
```

### File Watcher (watchfiles)

```python
# src/claude_code_web/watcher.py

from watchfiles import awatch, Change
from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async

async def start_watcher():
    channel_layer = get_channel_layer()
    watch_path = "/path/to/jsonl/files"
    
    async for changes in awatch(watch_path):
        for change_type, path in changes:
            if not path.endswith('.jsonl'):
                continue
            
            match change_type:
                case Change.added | Change.modified:
                    await process_jsonl_change(path, channel_layer)
                case Change.deleted:
                    await process_jsonl_delete(path, channel_layer)

async def process_jsonl_change(path, channel_layer):
    # Parse new lines, save to DB
    new_lines = await sync_to_async(parse_new_lines)(path)
    
    for line in new_lines:
        # Save to DB (triggers post_save signal)
        obj = await sync_to_async(save_to_db)(line)
        
        # Broadcast via WebSocket
        await channel_layer.group_send("updates", {
            "type": "send_update",
            "data": {
                "type": "new_message",
                "session_id": obj.session_id,
                "message": serialize_message(obj)
            }
        })
```

### Django Signals for Broadcasts

```python
# src/claude_code_web/core/signals.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

@receiver(post_save, sender=Session)
def on_session_save(sender, instance, created, **kwargs):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)("updates", {
        "type": "send_update",
        "data": {
            "type": "session_update",
            "session_id": str(instance.id),
            "data": {
                "message_count": instance.message_count,
                "cost": float(instance.cost),
                "title": instance.title,
            }
        }
    })
```

---

## Frontend

### Structure

```
frontend/
├── package.json
├── vite.config.js
├── src/
│   ├── main.js
│   ├── async.js
│   ├── wa.js
│   ├── App.vue
│   ├── stores/
│   │   ├── sessions.js
│   │   └── ui.js
│   ├── composables/
│   │   └── useUpdates.js
│   └── components/
│       ├── Sidebar.vue
│       ├── Conversation.vue
│       ├── CodeDiff.vue
│       └── messages/
│           ├── BaseMessage.vue
│           ├── MessageText.vue
│           ├── MessageToolUse.vue
│           ├── MessageToolResult.vue
│           └── index.js
└── dist/
```

### Vite Config

```javascript
// frontend/vite.config.js

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
    plugins: [
        vue({
            template: {
                compilerOptions: {
                    // Web Awesome custom elements
                    isCustomElement: (tag) => tag.startsWith('wa-')
                }
            }
        })
    ],
    build: {
        outDir: '../static/dist',
        manifest: true,
        rollupOptions: {
            input: 'src/main.js'
        }
    }
})
```

### Package.json

```json
{
    "scripts": {
        "dev": "vite",
        "build": "vite build"
    },
    "dependencies": {
        "vue": "^3.4",
        "pinia": "^2.1",
        "@vueuse/core": "^10.0",
        "@awesome.me/webawesome": "^3.0"
    },
    "devDependencies": {
        "@vitejs/plugin-vue": "^5.0",
        "vite": "^5.0"
    }
}
```

### Web Awesome Setup

```javascript
// frontend/src/wa.js

import '@awesome.me/webawesome/dist/styles/webawesome.css'

// Core components (loaded immediately)
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/input/input.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/badge/badge.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/drawer/drawer.js'

// Lazy loaded
export const loadDialog = () => import('@awesome.me/webawesome/dist/components/dialog/dialog.js')
```

### Store (Pinia + VueUse)

Pinia is the official state management library for Vue 3. It provides:
- Deep reactivity out of the box
- `$patch()` for deep merging server data into state
- DevTools integration for debugging
- TypeScript support

#### Why Pinia over plain `reactive()`?

The key feature is `$patch()` which performs **recursive deep merge**. When the backend sends a complete object via WebSocket, Pinia merges it intelligently:

```javascript
// Backend sends complete session object
store.$patch({
    sessions: {
        'abc123': { title: 'New', message_count: 43, cost: 0.15 }
    }
})
// → Only changed properties trigger re-renders
// → Existing properties not in the patch are preserved
```

This eliminates the need for external libraries like `vue-object-merge` or `fast-json-patch`.

#### Pinia Store Definition

```javascript
// frontend/src/stores/sessions.js

import { defineStore } from 'pinia'
import { useWebSocket } from '@vueuse/core'

export const useSessionsStore = defineStore('sessions', {
    state: () => ({
        sessions: {},
        messages: {},
        activeSessionId: null
    }),
    
    getters: {
        sessionList: (state) => Object.values(state.sessions),
        currentSession: (state) => state.sessions[state.activeSessionId],
        currentMessages: (state) => state.messages[state.activeSessionId] || []
    },
    
    actions: {
        // Deep merge session data from WebSocket
        updateSession(sessionId, data) {
            if (!this.sessions[sessionId]) {
                this.sessions[sessionId] = { id: sessionId }
            }
            // $patch does recursive deep merge
            this.$patch({
                sessions: { [sessionId]: data }
            })
        },
        
        addMessage(sessionId, message) {
            if (!this.messages[sessionId]) {
                this.messages[sessionId] = []
            }
            this.messages[sessionId].push(message)
            this.sessions[sessionId].message_count++
        },
        
        setActiveSession(id) {
            this.activeSessionId = id
        }
    }
})
```

#### UI State Store (persisted)

```javascript
// frontend/src/stores/ui.js

import { defineStore } from 'pinia'
import { useLocalStorage } from '@vueuse/core'

export const useUiStore = defineStore('ui', () => {
    // VueUse persists to localStorage automatically
    const collapsed = useLocalStorage('ui-collapsed', {})
    const scrollPositions = useLocalStorage('ui-scroll', {})
    
    function isCollapsed(msgId) {
        return collapsed.value[msgId] ?? false
    }
    
    function toggleCollapsed(msgId) {
        collapsed.value[msgId] = !isCollapsed(msgId)
    }
    
    function getScrollPosition(sessionId) {
        return scrollPositions.value[sessionId] ?? 0
    }
    
    function setScrollPosition(sessionId, pos) {
        scrollPositions.value[sessionId] = pos
    }
    
    return {
        collapsed,
        scrollPositions,
        isCollapsed,
        toggleCollapsed,
        getScrollPosition,
        setScrollPosition
    }
})
```

#### WebSocket Handler

```javascript
// frontend/src/composables/useUpdates.js

import { useWebSocket } from '@vueuse/core'
import { useSessionsStore } from '../stores/sessions'

export function useUpdates() {
    const store = useSessionsStore()
    
    const { status, send } = useWebSocket(`ws://${location.host}/ws/`, {
        autoReconnect: {
            retries: 5,
            delay: 1000
        },
        heartbeat: {
            message: 'ping',
            interval: 30000
        },
        onMessage(ws, event) {
            const msg = JSON.parse(event.data)
            handleMessage(msg)
        }
    })
    
    function handleMessage(msg) {
        switch (msg.type) {
            case 'session_update':
                // $patch deep merges the data
                store.updateSession(msg.session_id, msg.data)
                break
            
            case 'new_message':
                store.addMessage(msg.session_id, msg.message)
                break
            
            case 'session_created':
                store.$patch({
                    sessions: { [msg.session.id]: msg.session },
                    messages: { [msg.session.id]: [] }
                })
                break
        }
    }
    
    function sendMessage(content) {
        send(JSON.stringify({ type: 'user_message', content }))
    }
    
    return { status, sendMessage }
}
```

#### Main Entry Point

```javascript
// frontend/src/main.js

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './wa.js'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')

// Initial data load
import { useSessionsStore } from './stores/sessions'
const store = useSessionsStore()
fetch('/api/sessions/')
    .then(res => res.json())
    .then(sessions => {
        for (const session of sessions) {
            store.sessions[session.id] = session
        }
    })
```

#### $patch Behavior Details

```javascript
// Object syntax: deep recursive merge
store.$patch({
    sessions: {
        'abc': { message_count: 43 }  // merges with existing session
    }
})

// Function syntax: direct mutation (for arrays, replacements)
store.$patch((state) => {
    state.messages['abc'].push(newMessage)  // array push
    state.sessions['xyz'] = completeNewSession  // full replacement
})

// Edge case: empty object does nothing (by design)
store.$patch({ sessions: { 'abc': {} } })  // no effect

// Edge case: to clear/replace, use function syntax
store.$patch((state) => {
    state.sessions['abc'] = {}  // actually clears
})
```

### Async Components Registry

```javascript
// frontend/src/async.js

import { defineAsyncComponent } from 'vue'

export const AsyncCodeDiff = defineAsyncComponent(() => 
    import('./components/CodeDiff.vue')
)

export const AsyncTerminal = defineAsyncComponent(() => 
    import('./components/Terminal.vue')
)

export const AsyncMarkdownPreview = defineAsyncComponent(() => 
    import('./components/MarkdownPreview.vue')
)
```

### Main App with KeepAlive

```vue
<!-- frontend/src/App.vue -->

<template>
    <div class="app">
        <Sidebar />
        
        <main class="main">
            <!-- KeepAlive preserves conversation state (scroll, collapsed, etc.) -->
            <KeepAlive :max="5">
                <Conversation 
                    v-if="currentSession"
                    :key="currentSession.id" 
                    :session-id="currentSession.id" 
                />
            </KeepAlive>
            
            <div v-else class="empty-state">
                Select a conversation
            </div>
        </main>
    </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionsStore } from './stores/sessions'
import Sidebar from './components/Sidebar.vue'
import Conversation from './components/Conversation.vue'

const store = useSessionsStore()
const currentSession = computed(() => store.currentSession)
</script>
```

### Base Message Component

```vue
<!-- frontend/src/components/messages/BaseMessage.vue -->

<template>
    <div class="message" :class="[`message--${type}`, { collapsed }]">
        <div class="message__header" @click="collapsed = !collapsed">
            <span class="message__icon">
                <slot name="icon">💬</slot>
            </span>
            <span class="message__title">
                <slot name="title">Message</slot>
            </span>
            <span class="message__meta">
                <slot name="meta">
                    <time>{{ formatTime(message.timestamp) }}</time>
                </slot>
            </span>
            <wa-icon-button name="chevron-down" :class="{ rotated: collapsed }" />
        </div>
        
        <div class="message__body" v-show="!collapsed">
            <slot></slot>
        </div>
        
        <div class="message__footer" v-if="$slots.footer">
            <slot name="footer"></slot>
        </div>
    </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
    message: { type: Object, required: true },
    type: { type: String, default: 'default' },
    startCollapsed: { type: Boolean, default: false }
})

const collapsed = ref(props.startCollapsed)

function formatTime(ts) {
    return new Date(ts).toLocaleTimeString()
}
</script>
```

### Message Component with Lazy Loaded Diff

```vue
<!-- frontend/src/components/messages/MessageToolResult.vue -->

<template>
    <BaseMessage :message="message" type="tool_result" :start-collapsed="!message.is_error">
        <template #icon>{{ message.is_error ? '❌' : '✅' }}</template>
        <template #title>Result: <code>{{ message.tool_name }}</code></template>
        
        <div v-if="message.has_diff">
            <wa-button v-if="!showDiff" @click="showDiff = true" size="small">
                Show diff
            </wa-button>
            
            <Suspense v-if="showDiff">
                <template #default>
                    <AsyncCodeDiff 
                        :old-code="message.old_code" 
                        :new-code="message.new_code"
                        :language="message.language"
                    />
                </template>
                <template #fallback>
                    <wa-spinner></wa-spinner>
                </template>
            </Suspense>
        </div>
        
        <div v-else>
            <pre><code>{{ message.output }}</code></pre>
        </div>
    </BaseMessage>
</template>

<script setup>
import { ref } from 'vue'
import BaseMessage from './BaseMessage.vue'
import { AsyncCodeDiff } from '../../async.js'

defineProps({
    message: { type: Object, required: true }
})

const showDiff = ref(false)
</script>
```

### Message Components Registry

```javascript
// frontend/src/components/messages/index.js

import MessageText from './MessageText.vue'
import MessageToolUse from './MessageToolUse.vue'
import MessageToolResult from './MessageToolResult.vue'
import MessageError from './MessageError.vue'

export const messageComponents = {
    'user': MessageText,
    'assistant': MessageText,
    'tool_use': MessageToolUse,
    'tool_result': MessageToolResult,
    'error': MessageError,
}

export function getMessageComponent(type) {
    return messageComponents[type] || MessageText
}
```

### Conversation with Dynamic Message Types

```vue
<!-- frontend/src/components/Conversation.vue -->

<template>
    <div class="conversation">
        <header class="conversation__header">
            <h1>{{ session.title }}</h1>
            <wa-badge variant="neutral">{{ session.message_count }} messages</wa-badge>
            <wa-badge variant="primary">{{ session.cost }} $</wa-badge>
        </header>
        
        <div ref="messagesContainer" class="conversation__messages">
            <component 
                v-for="msg in messages" 
                :key="msg.id"
                :is="getComponent(msg.type)"
                :message="msg"
            />
        </div>
        
        <footer class="conversation__input">
            <wa-textarea 
                :value="input" 
                @wa-input="input = $event.target.value"
                @keydown.ctrl.enter="send"
                placeholder="Message..."
            ></wa-textarea>
            <wa-button variant="primary" @click="send">Send</wa-button>
        </footer>
    </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useScroll } from '@vueuse/core'
import { useSessionsStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { useUpdates } from '../composables/useUpdates'
import { getMessageComponent } from './messages'

const props = defineProps({
    sessionId: { type: String, required: true }
})

const sessionsStore = useSessionsStore()
const uiStore = useUiStore()
const { sendMessage } = useUpdates()

const messagesContainer = ref(null)
const input = ref('')

const session = computed(() => sessionsStore.currentSession)
const messages = computed(() => sessionsStore.currentMessages)

const { arrivedState } = useScroll(messagesContainer)

// Auto-scroll on new message (only if already at bottom)
watch(messages, () => {
    if (arrivedState.bottom) {
        nextTick(() => {
            messagesContainer.value?.scrollTo({ top: messagesContainer.value.scrollHeight })
        })
    }
}, { deep: true })

// Restore scroll position
onMounted(() => {
    const savedPos = uiStore.getScrollPosition(props.sessionId)
    if (savedPos && messagesContainer.value) {
        messagesContainer.value.scrollTop = savedPos
    }
})

// Save scroll position
onBeforeUnmount(() => {
    if (messagesContainer.value) {
        uiStore.setScrollPosition(props.sessionId, messagesContainer.value.scrollTop)
    }
})

function getComponent(type) {
    return getMessageComponent(type)
}

function send() {
    if (!input.value.trim()) return
    sendMessage(input.value)
    input.value = ''
}
</script>
```

---

## Key Concepts

### Vue Reactivity (ref vs computed)

```javascript
import { ref, computed } from 'vue'

// ref: mutable reactive variable
const count = ref(0)
count.value++  // Vue detects change → UI updates

// computed: derived value, auto-recalculated
const doubled = computed(() => count.value * 2)
// doubled.value is always count * 2, recalculated only when count changes
```

### Granular Reactivity

Vue tracks dependencies at the property level:

```javascript
state.sessions['abc'].message_count = 43

// Only components reading message_count re-render
// Components reading title, cost, etc. → NOT re-rendered
```

### Store Updates from WebSocket (Pinia $patch)

```
WebSocket message arrives
    → store.$patch({ sessions: { [id]: data } })
    → Pinia deep merges with existing state
    → Only changed properties trigger re-renders
    → Components in KeepAlive cache also update (still reactive)
    → No manual DOM manipulation needed
```

**$patch limitations to know:**
- `$patch({ prop: {} })` with empty object → no effect (by design)
- Array mutations (push, splice) → use function syntax: `$patch((state) => ...)`
- Full replacement → use function syntax: `$patch((state) => state.x = newValue)`

### KeepAlive Behavior

- Cached conversations stay reactive to store changes
- Scroll position, collapsed states preserved when switching
- `onActivated`/`onDeactivated` hooks for special logic
- `:max="5"` limits memory usage

---

## Launch Commands

```bash
# Development (from source)
./run.py

# With custom port
./run.py 8080

# Via uvx (no prior install)
uvx claude-code-web

# From GitHub
uvx --from git+https://github.com/user/claude-code-web claude-code-web
```

---

## Uvicorn Configuration

```bash
# Basic
uvicorn project.asgi:application

# Custom port + bind to network
uvicorn project.asgi:application --host 0.0.0.0 --port 8080
```

Supports multiple WebSocket connections (user on PC + phone + tablet simultaneously).

**Why Uvicorn over Daphne?** Uvicorn uses native Python asyncio, making it simpler to integrate with asyncio-based file watchers like `watchfiles`. Daphne uses Twisted, which requires complex reactor management for asyncio interop.
