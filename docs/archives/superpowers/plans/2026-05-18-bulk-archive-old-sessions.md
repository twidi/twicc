# Bulk archive old sessions — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter une action « Archive sessions older than… » dans le dropdown *session list options* de la sidebar, ouvrant un dialog qui archive en bulk toutes les sessions du scope courant (projet / workspace / all-projects) plus anciennes que la durée choisie.

**Architecture :** Un endpoint backend sync `POST /api/sessions/bulk-archive/` qui filtre + UPDATE en SQL + broadcast WS unique (`sessions_bulk_archived`) + post-work non-bloquant (reindex Tantivy + cleanup tmux) dans un thread daemon. Côté frontend, un nouvel item dans le dropdown ouvre un dialog avec sélecteur de durée live (re-dry-run à chaque changement), et le handler WS applique l'archivage local sans rappeler le backend.

**Tech Stack :** Django (sync views) + Channels + SQLite + Tantivy + Vue 3 (Composition API) + Pinia + Web Awesome 3 (`wa-dropdown`, `wa-dropdown-item slot="submenu"`, `wa-dialog`, `wa-select`).

**Spec :** [`docs/superpowers/specs/2026-05-18-bulk-archive-old-sessions-design.md`](../specs/2026-05-18-bulk-archive-old-sessions-design.md). Toute divergence par rapport à la spec doit être justifiée ou soulevée — pas de drift silencieux.

**Note importante :** Ce projet n'a pas de tests automatisés (cf. `CLAUDE.md` : « no tests and no linting »). Les vérifications sont manuelles via le navigateur. Vite HMR rafraîchit automatiquement le frontend ; le backend doit être redémarré par l'**utilisateur** (le subagent n'a pas le droit de redémarrer les serveurs — cf. mémoire `feedback_never_restart_servers.md`).

---

## File Structure

### Files to create

| Path | Responsibility |
|------|----------------|
| `frontend/src/utils/datePresets.js` | Util partagé : liste `DURATION_PRESETS` (value + label) + fonction `presetToDate(preset)` retournant un ISO timestamp dans le passé. Exporté pour `SearchOverlay.vue` (refactor) et `BulkArchiveConfirmDialog.vue` (nouveau). |
| `frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue` | Dialog de confirmation modal. Affiche le scope (project/workspace/all), un `<wa-select>` de durée présélectionné, un compte (récupéré par dry-run, mis à jour à chaque changement de durée), un bouton « Archive N ». Émet `archived` au succès. |

### Files to modify

| Path | Changes |
|------|---------|
| `src/twicc/views.py` | Ajout de la fonction `bulk_archive_sessions(request)` (vue sync). Imports requis : `threading`, `async_to_sync`, `read_workspaces`, `SessionType`, `get_agent_manager_registry`, `kill_all_tmux_terminals`, `search`, `datetime`. |
| `src/twicc/urls.py` | Ajout de la route `path("api/sessions/bulk-archive/", views.bulk_archive_sessions)`. |
| `frontend/src/components/app/SearchOverlay.vue` | Remplacer la définition locale de `presetToDate` (≈ lignes 150-164) par un import depuis `../../utils/datePresets`. |
| `frontend/src/views/ProjectView.vue` | (a) Ajouter `<wa-dropdown-item value="archive-older">` avec sous-menu de 8 entrées dans le dropdown des session list options ; (b) brancher `value.startsWith('archive-older-')` dans `handleSessionOptionsSelect` ; (c) state local `bulkArchiveDialog` + monter `<BulkArchiveConfirmDialog>` ; (d) `useToast()` au succès. |
| `frontend/src/stores/data.js` | (a) Action `bulkArchiveSessions({ olderThan, scope, dryRun, signal })` qui appelle l'endpoint via `apiFetch` ; (b) Handler `applyBulkArchiveFromBroadcast(sessionIds)` purement local (état + `removeMruSession`), **sans** rappel HTTP. |
| `frontend/src/composables/useWebSocket.js` | Ajouter un `case 'sessions_bulk_archived':` dans le switch sur `message.type` (≈ après le case `session_updated`, autour de la ligne 743). |
| `frontend/public/tips/archive-sessions.md` | Étendre la première phrase pour mentionner le bulk archive ; corriger la phrase de fin (« next to the sidebar session filter » → « session list options menu »). |

### Order rationale

1. **Task 1 — refactor pur** (extraction `presetToDate`). Aucune nouvelle fonctionnalité, validable de manière isolée.
2. **Task 2 — backend endpoint seul.** Testable via fetch dans la console du navigateur, sans UI.
3. **Task 3 — wiring WS + store.** Pas testable de bout en bout sans Task 5, mais commit séparé pour granularité.
4. **Task 4 — dialog isolé.** Composant nouveau, sans dépendance.
5. **Task 5 — câblage final (UI complète).** Premier test end-to-end utilisateur.
6. **Task 6 — tip mis à jour.** Doc.

---

## Task 1 : Extract `presetToDate` to shared util

**Files :**
- Create : `frontend/src/utils/datePresets.js`
- Modify : `frontend/src/components/app/SearchOverlay.vue` (≈ lignes 150-164)

- [ ] **Step 1 : Create `frontend/src/utils/datePresets.js`**

```js
// Durations partagées entre le full-text search et le bulk archive.
// L'ordre détermine l'ordre d'affichage dans les listes.
export const DURATION_PRESETS = [
    { value: '3d',  label: '3 days' },
    { value: '7d',  label: '7 days' },
    { value: '10d', label: '10 days' },
    { value: '20d', label: '20 days' },
    { value: '30d', label: '30 days' },
    { value: '2m',  label: '2 months' },
    { value: '3m',  label: '3 months' },
    { value: '6m',  label: '6 months' },
]

/**
 * Convertit un preset de durée (e.g. '7d', '3m') en timestamp ISO dans le passé.
 * Retourne null si le preset est inconnu.
 */
export function presetToDate(preset) {
    const d = new Date()
    switch (preset) {
        case '3d':  d.setDate(d.getDate() - 3); break
        case '7d':  d.setDate(d.getDate() - 7); break
        case '10d': d.setDate(d.getDate() - 10); break
        case '20d': d.setDate(d.getDate() - 20); break
        case '30d': d.setDate(d.getDate() - 30); break
        case '2m':  d.setMonth(d.getMonth() - 2); break
        case '3m':  d.setMonth(d.getMonth() - 3); break
        case '6m':  d.setMonth(d.getMonth() - 6); break
        default: return null
    }
    return d.toISOString()
}
```

- [ ] **Step 2 : Replace local `presetToDate` in `SearchOverlay.vue`**

Dans `frontend/src/components/app/SearchOverlay.vue`, ajouter en haut du `<script setup>` (à côté des autres imports) :

```js
import { presetToDate } from '../../utils/datePresets'
```

Puis **supprimer** la définition locale de `presetToDate` (≈ lignes 149-164 du fichier actuel — le commentaire « Convert a duration preset… » et la fonction).

- [ ] **Step 3 : Manual smoke test (frontend HMR auto-reload)**

User opens the full-text search overlay (Ctrl+Shift+F) and selects an entry in **Older than** and an entry in **Newer than**. Results should filter exactly as before.

- [ ] **Step 4 : Commit**

```bash
git add frontend/src/utils/datePresets.js frontend/src/components/app/SearchOverlay.vue
git commit -m "$(cat <<'EOF'
refactor(frontend): extract date presets to shared util

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : Backend bulk-archive endpoint

**Files :**
- Modify : `src/twicc/views.py` (ajout de fonction)
- Modify : `src/twicc/urls.py` (ajout de route)

- [ ] **Step 1 : Add the view function to `src/twicc/views.py`**

Au début du fichier (`src/twicc/views.py:1-29`), les imports actuels sont :

```python
import logging
import os
from bisect import bisect_left
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone

import orjson

from twicc import search
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AgentLink, Command, DailyActivity, PinMode, Project, Session, SessionItem, SessionType, ToolResultLink, UsageSnapshot, WeeklyActivity
from twicc.core.serializers import (...)
from twicc.paths import path_to_project_id
from twicc.projects import register_project_sync
from twicc.providers.state import ProviderDisabledError, ensure_provider_running
from twicc.providers.helpers import get_provider_helpers, get_provider_helpers_registry
```

**Already imported, do not re-add** : `Session`, `SessionType`, `JsonResponse`, `orjson`, `search`, `datetime`.

**Imports to add** at the appropriate locations in the existing import groups :

```python
# add to stdlib group
import threading

# add to django.http import line
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse

# add to third-party group
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# add to twicc group
from twicc.agent.registry import get_agent_manager_registry
from twicc.terminal import kill_all_tmux_terminals
from twicc.workspaces import read_workspaces
```

Ajouter la fonction à la fin de la zone des vues sessions (après `session_detail`, avant la zone subagents/git si présent — emplacement logique pour la cohérence du fichier) :

```python
def bulk_archive_sessions(request):
    """POST /api/sessions/bulk-archive/ - Archive multiple sessions in one shot.

    Body:
        older_than (str, required): ISO timestamp. Sessions with mtime < this are eligible.
        scope (str, required): 'project' | 'workspace' | 'all'.
        project_id (str): required if scope == 'project'.
        workspace_id (str): required if scope == 'workspace'.
        dry_run (bool, optional, default False): if True, return only the count.

    Excludes: subagents, already-archived, pinned, sessions with an active agent,
    and sessions without user messages or without created_at (not visible in sidebar).

    Returns: {"count": N}. Sessions IDs are not in the response — the frontend
    receives them via the `sessions_bulk_archived` WS broadcast.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    older_than_iso = data.get("older_than")
    scope_type = data.get("scope")
    project_id = data.get("project_id")
    workspace_id = data.get("workspace_id")
    dry_run = bool(data.get("dry_run", False))

    if not older_than_iso:
        return JsonResponse({"error": "older_than is required"}, status=400)
    if scope_type not in ("project", "workspace", "all"):
        return JsonResponse({"error": "Invalid scope"}, status=400)
    if scope_type == "project" and not project_id:
        return JsonResponse({"error": "project_id required for scope=project"}, status=400)
    if scope_type == "workspace" and not workspace_id:
        return JsonResponse({"error": "workspace_id required for scope=workspace"}, status=400)

    try:
        older_than_epoch = datetime.fromisoformat(
            older_than_iso.replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        return JsonResponse({"error": "Invalid older_than format"}, status=400)

    active_ids = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }

    qs = Session.objects.filter(
        type=SessionType.SESSION,
        user_message_count__gt=0,
        created_at__isnull=False,
        archived=False,
        pinned__isnull=True,
        mtime__lt=older_than_epoch,
    ).exclude(id__in=active_ids)

    if scope_type == "project":
        qs = qs.filter(project_id=project_id)
    elif scope_type == "workspace":
        ws_data = read_workspaces()
        ws = next(
            (w for w in ws_data.get("workspaces", []) if w["id"] == workspace_id),
            None,
        )
        if ws is None:
            return JsonResponse({"error": "Workspace not found"}, status=404)
        qs = qs.filter(project_id__in=ws.get("projectIds", []))
    # scope_type == "all": no additional filter

    if dry_run:
        return JsonResponse({"count": qs.count()})

    # Capture IDs before UPDATE (queryset becomes empty after).
    # Re-check active_ids just before UPDATE to close the TOCTOU window.
    ids = set(qs.values_list("id", flat=True))
    active_ids_now = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }
    ids -= active_ids_now

    Session.objects.filter(id__in=ids).update(archived=True)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)("updates", {
        "type": "broadcast",
        "data": {
            "type": "sessions_bulk_archived",
            "session_ids": list(ids),
        },
    })

    if ids:
        ids_snapshot = list(ids)

        def post_archive_work():
            for sid in ids_snapshot:
                try:
                    if search.is_initialized():
                        search.reindex_session(sid)
                except Exception:
                    pass
                try:
                    kill_all_tmux_terminals(f"s:{sid}")
                except Exception:
                    pass

        threading.Thread(target=post_archive_work, daemon=True).start()

    return JsonResponse({"count": len(ids)})
```

- [ ] **Step 2 : Register the route in `src/twicc/urls.py`**

Dans `urlpatterns`, ajouter une ligne près de `api/sessions/` (ligne 16-17) :

```python
path("api/sessions/bulk-archive/", views.bulk_archive_sessions),
```

Emplacement exact : juste après la ligne `path("api/sessions/<str:session_id>/", views.session_by_id),`.

- [ ] **Step 3 : Ask the user to restart the backend**

> **Action utilisateur requise :** redémarrer le backend pour charger les changements. `uv run ./devctl.py restart back` depuis le projet, ou laisser le subagent rappeler ce point dans le wrap-up.

Le subagent **ne doit pas** lancer `devctl.py restart` lui-même (cf. mémoire `feedback_never_restart_servers.md`).

- [ ] **Step 4 : Smoke test via browser devtools console**

User opens devtools console on a logged-in TwiCC tab, then runs successively :

```js
// Dry-run on 'all' scope, very far in the past — should match 0 sessions
await fetch('/api/sessions/bulk-archive/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ older_than: '1970-01-01T00:00:00Z', scope: 'all', dry_run: true }),
}).then(r => r.json())
// Expected: { count: 0 }

// Dry-run on 'all' scope, far in the future — should match all eligible sessions
await fetch('/api/sessions/bulk-archive/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ older_than: '2999-01-01T00:00:00Z', scope: 'all', dry_run: true }),
}).then(r => r.json())
// Expected: { count: N } where N > 0

// Error cases
await fetch('/api/sessions/bulk-archive/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope: 'all', dry_run: true }),
}).then(r => r.json())
// Expected: { error: "older_than is required" }, status 400

await fetch('/api/sessions/bulk-archive/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ older_than: '2999-01-01T00:00:00Z', scope: 'workspace' }),
}).then(r => r.json())
// Expected: { error: "workspace_id required for scope=workspace" }, status 400
```

**Do not** test the non-dry-run path in this step — that would archive real sessions before the frontend handler exists (next tasks), making cleanup tedious. The dry-run path exercises the same filter logic.

- [ ] **Step 5 : Commit**

```bash
git add src/twicc/views.py src/twicc/urls.py
git commit -m "$(cat <<'EOF'
feat(archive): add bulk-archive endpoint

POST /api/sessions/bulk-archive/ filters sessions older than a given
timestamp, scoped to project / workspace / all, and archives them in a
single UPDATE. Excludes pinned, active, and non-visible sessions.
Broadcasts a single sessions_bulk_archived WS message and runs the
Tantivy reindex + tmux cleanup in a daemon thread.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 : WebSocket handler + store actions

**Files :**
- Modify : `frontend/src/composables/useWebSocket.js` (ajouter un case)
- Modify : `frontend/src/stores/data.js` (deux ajouts)

- [ ] **Step 1 : Add the WS case in `useWebSocket.js`**

Dans `frontend/src/composables/useWebSocket.js`, localiser le `switch (message.type)` (autour de la ligne 743, là où le case `'session_updated'` est défini). Ajouter juste après ce case :

```js
case 'sessions_bulk_archived':
    store.applyBulkArchiveFromBroadcast(message.session_ids)
    break
```

`store` est déjà la variable utilisée par les cases voisins (ex. `store.updateSession(...)`).

- [ ] **Step 2 : Add `applyBulkArchiveFromBroadcast` to `data.js`**

Dans `frontend/src/stores/data.js`, dans la section `actions` (chercher `setSessionArchived` autour de la ligne 3174 pour repérer l'emplacement). Ajouter immédiatement après cette action :

```js
        /**
         * Apply a bulk-archive broadcast from the backend. Local-only:
         * marks sessions as archived in the store and removes them from MRU.
         * Does NOT call the backend (the backend already archived them).
         * Does NOT touch pinned sessions (the backend filtered them out).
         */
        applyBulkArchiveFromBroadcast(sessionIds) {
            for (const sid of sessionIds) {
                const session = this.sessions[sid]
                if (session) {
                    session.archived = true
                }
                this.removeMruSession(sid)
            }
        },
```

- [ ] **Step 3 : Add `bulkArchiveSessions` action to `data.js`**

Toujours dans `frontend/src/stores/data.js`, immédiatement après `applyBulkArchiveFromBroadcast`, ajouter l'action qui appelle l'endpoint :

```js
        /**
         * Call the bulk-archive endpoint.
         *
         * @param {Object} params
         * @param {string} params.olderThan   - ISO timestamp threshold.
         * @param {Object} params.scope       - { type: 'project'|'workspace'|'all', id: string|null }.
         * @param {boolean} [params.dryRun]   - If true, returns only the count.
         * @param {AbortSignal} [params.signal] - Abort signal for cancellable dry-runs.
         * @returns {Promise<{count: number}>}
         */
        async bulkArchiveSessions({ olderThan, scope, dryRun = false, signal = null }) {
            const body = {
                older_than: olderThan,
                scope: scope.type,
                dry_run: dryRun,
            }
            if (scope.type === 'project') body.project_id = scope.id
            if (scope.type === 'workspace') body.workspace_id = scope.id

            const res = await apiFetch('/api/sessions/bulk-archive/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal,
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.error || `HTTP ${res.status}`)
            }
            return res.json()
        },
```

Vérifier que `apiFetch` est déjà importé en haut de `data.js` (devrait l'être : utilisé par 14+ autres actions).

- [ ] **Step 4 : Quick smoke test via browser devtools console**

Even without UI, exercise the store action directly to catch wiring bugs before Task 5 :

```js
// In TwiCC's tab devtools console (frontend HMR has reloaded already)
const dataStore = window.__pinia?.state?.value?.data
    ? (await import('/src/stores/data.js')).useDataStore()
    : null
// Or simply, if you have a component instance handy via Vue devtools:
// pick any component, then in its console:
//   $vm.$store // not Pinia... use the explicit import pattern below

// Simpler: just call the underlying fetch the action wraps,
// which validates the broadcast + handler wiring end-to-end:
const before = Object.values(usePiniaDataStoreOrEquivalent().sessions).filter(s => s.archived).length
// (skip this test if devtools access is awkward — Task 5 will exercise it end-to-end)
```

In practice the easiest validation is to **trigger a real archive in Task 5 and confirm the sidebar updates without a manual reload** — that proves the broadcast handler works. If you'd rather not run any test here, that's acceptable since Task 5 covers it.

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/composables/useWebSocket.js frontend/src/stores/data.js
git commit -m "$(cat <<'EOF'
feat(archive): add bulk-archive store action and WS handler

bulkArchiveSessions hits the new endpoint; applyBulkArchiveFromBroadcast
mutates local state only (archived flag + MRU removal) — no HTTP, no pin
manipulation. Wired to the new sessions_bulk_archived WS message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 : `BulkArchiveConfirmDialog.vue` component

**Files :**
- Create : `frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue`

- [ ] **Step 1 : Create the dialog component**

Créer `frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue` :

```vue
<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { DURATION_PRESETS, presetToDate } from '../../utils/datePresets'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    open: { type: Boolean, required: true },
    preset: { type: String, required: true },        // e.g. '7d'
    scope: { type: Object, required: true },         // { type, id }
})

const emit = defineEmits(['update:open', 'archived'])

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

const currentPreset = ref(props.preset)
watch(() => props.preset, (v) => { currentPreset.value = v })

const count = ref(null)         // null = loading, number = result
const error = ref(null)
const submitting = ref(false)

let abortController = null

const currentLabel = computed(
    () => DURATION_PRESETS.find((p) => p.value === currentPreset.value)?.label ?? currentPreset.value,
)

const workspace = computed(() => {
    if (props.scope.type !== 'workspace') return null
    return workspacesStore.workspaces.find((w) => w.id === props.scope.id) ?? null
})

const workspaceProjectIds = computed(() => {
    if (!workspace.value) return []
    return workspacesStore.getVisibleProjectIds(workspace.value.id)
})

async function refreshCount() {
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        // Defensive: shouldn't happen since wa-select only emits known presets,
        // but bail gracefully rather than send a malformed request.
        error.value = `Unknown duration preset: ${currentPreset.value}`
        return
    }

    if (abortController) abortController.abort()
    abortController = new AbortController()

    count.value = null
    error.value = null

    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
            dryRun: true,
            signal: abortController.signal,
        })
        count.value = res.count
    } catch (err) {
        if (err.name === 'AbortError') return
        error.value = err.message || 'Failed to fetch count.'
    }
}

watch(() => props.open, (isOpen) => {
    if (isOpen) {
        currentPreset.value = props.preset
        refreshCount()
    }
})

watch(currentPreset, () => {
    if (props.open) refreshCount()
})

onUnmounted(() => {
    if (abortController) abortController.abort()
})

async function handleConfirm() {
    if (count.value === 0 || count.value === null || submitting.value) return
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        error.value = `Unknown duration preset: ${currentPreset.value}`
        return
    }
    submitting.value = true
    error.value = null
    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
        })
        emit('archived', { count: res.count })
        emit('update:open', false)
    } catch (err) {
        error.value = err.message || 'Failed to archive sessions.'
    } finally {
        submitting.value = false
    }
}

function handleCancel() {
    emit('update:open', false)
}

// Wire the submit button to the form by id (Web Awesome wa-button does not
// expose `form` as a property — must be set via setAttribute on the host).
const submitButtonRef = ref(null)
const FORM_ID = 'bulk-archive-confirm-form'
watch(submitButtonRef, async (el) => {
    if (el) {
        await nextTick()
        el.setAttribute('form', FORM_ID)
    }
})

function handleDialogHide() {
    if (props.open) emit('update:open', false)
}
</script>

<template>
    <wa-dialog
        :open="open"
        label="Archive sessions"
        @wa-hide="handleDialogHide"
        style="--width: min(520px, calc(100vw - 2rem));"
    >
        <form :id="FORM_ID" @submit.prevent="handleConfirm">
            <div class="bulk-archive-row">
                <span>Archive sessions older than</span>
                <wa-select v-model="currentPreset" size="small" class="duration-select">
                    <wa-option
                        v-for="p in DURATION_PRESETS"
                        :key="p.value"
                        :value="p.value"
                    >{{ p.label }}</wa-option>
                </wa-select>
            </div>

            <div class="bulk-archive-scope">
                <div class="bulk-archive-scope-label">Scope:</div>
                <div v-if="scope.type === 'project'">
                    <ProjectBadge :project-id="scope.id" />
                </div>
                <div v-else-if="scope.type === 'workspace' && workspace">
                    <div class="workspace-header">
                        <wa-icon
                            name="layer-group"
                            auto-width
                            :style="workspace.color ? { color: workspace.color } : null"
                        ></wa-icon>
                        <span>{{ workspace.name }}</span>
                    </div>
                    <div class="workspace-projects">
                        <ProjectBadge
                            v-for="pid in workspaceProjectIds"
                            :key="pid"
                            :project-id="pid"
                        />
                    </div>
                </div>
                <div v-else>All projects</div>
            </div>

            <div class="bulk-archive-count">
                <template v-if="count === null && !error">
                    <wa-spinner></wa-spinner>
                    <span>Counting…</span>
                </template>
                <template v-else-if="count === 0">
                    No sessions to archive in this scope older than {{ currentLabel }}.
                </template>
                <template v-else-if="count !== null">
                    This will archive <strong>{{ count }}</strong> session{{ count > 1 ? 's' : '' }}.
                </template>
            </div>

            <div class="bulk-archive-hint">
                Sessions that are pinned or have an active process are excluded.
            </div>

            <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>
        </form>

        <wa-button slot="footer" appearance="plain" @click="handleCancel">
            Cancel
        </wa-button>
        <wa-button
            slot="footer"
            ref="submitButtonRef"
            type="submit"
            variant="brand"
            :disabled="count === null || count === 0 || submitting"
            :loading="submitting"
        >
            <template v-if="count && count > 0">Archive {{ count }}</template>
            <template v-else>Archive</template>
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
.bulk-archive-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-m);
    flex-wrap: wrap;
}

.duration-select {
    min-width: 8rem;
}

.bulk-archive-scope {
    margin-bottom: var(--wa-space-m);
}

.bulk-archive-scope-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    margin-bottom: var(--wa-space-2xs);
}

.workspace-header {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-2xs);
}

.workspace-projects {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}

.bulk-archive-count {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-xs);
    min-height: 1.5rem;
}

.bulk-archive-hint {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
```

- [ ] **Step 2 : Verify Web Awesome component imports**

Le composant utilise `<wa-dialog>`, `<wa-select>`, `<wa-option>`, `<wa-spinner>`, `<wa-callout>`, `<wa-button>`, `<wa-icon>`. Vérifier que tous sont déjà importés dans `frontend/src/main.js` (cf. `CLAUDE.md` : « Each Web Awesome component used must be explicitly imported in `frontend/src/main.js` »).

Au moment de la rédaction de ce plan, **tous les composants ci-dessus sont déjà importés** dans `main.js` (vérifié — notamment `wa-spinner` à la ligne 24). Cette étape est donc principalement une re-vérification défensive :

```bash
for comp in dialog select option spinner callout button icon; do
    grep -q "components/$comp/" frontend/src/main.js || echo "MISSING: $comp"
done
```

Si la commande affiche `MISSING: <comp>`, ajouter l'import correspondant en suivant le modèle des autres imports du fichier.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue
git commit -m "$(cat <<'EOF'
feat(archive): add BulkArchiveConfirmDialog component

Shows scope (project / workspace / all), a duration select that triggers
a fresh dry-run on each change, an exact count, and a confirmation button.
Cancels in-flight dry-runs with AbortController. Emits 'archived' on success.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 : Wire up the dropdown menu and dialog in `ProjectView.vue`

**Files :**
- Modify : `frontend/src/views/ProjectView.vue`

- [ ] **Step 1 : Import the dialog and `useToast`**

Dans `frontend/src/views/ProjectView.vue`, ajouter en haut du `<script setup>` (avec les autres imports) :

```js
import BulkArchiveConfirmDialog from '../components/sidebar/BulkArchiveConfirmDialog.vue'
import { useToast } from '../composables/useToast'
```

(Vérifier le chemin exact de `useToast` selon où il est défini dans le projet ; ajuster si besoin.)

- [ ] **Step 2 : Add the menu item with submenu**

Repérer le bloc `<wa-dropdown ... class="session-options-dropdown" ... >` (≈ ligne 1236). Insérer juste après l'item `value="show-archived"` (≈ ligne 1256, avant l'item `compact-view`) :

```html
                        <wa-dropdown-item value="archive-older">
                            <wa-icon slot="icon" name="box-archive"></wa-icon>
                            Archive sessions older than…
                            <wa-dropdown-item slot="submenu" value="archive-older-3d">3 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-7d">7 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-10d">10 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-20d">20 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-30d">30 days</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-2m">2 months</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-3m">3 months</wa-dropdown-item>
                            <wa-dropdown-item slot="submenu" value="archive-older-6m">6 months</wa-dropdown-item>
                        </wa-dropdown-item>
```

- [ ] **Step 3 : Add the dialog state, scope helper, and handler logic**

Dans le `<script setup>` de `ProjectView.vue`, repérer la fonction `handleSessionOptionsSelect` (`grep -n "handleSessionOptionsSelect" frontend/src/views/ProjectView.vue` — devrait être autour de la ligne 501 d'après l'exploration initiale).

Ajouter, juste **avant** `handleSessionOptionsSelect`, le state du dialog et le helper de scope :

```js
const bulkArchiveDialog = ref({ open: false, preset: '7d' })
const toast = useToast()

// Computed plutôt qu'appel inline dans le template, pour ne pas re-construire
// l'objet à chaque render et conserver une référence stable.
const currentBulkArchiveScope = computed(() => {
    if (!isAllProjectsMode.value) {
        return { type: 'project', id: projectId.value }
    }
    if (activeWorkspaceId.value) {
        return { type: 'workspace', id: activeWorkspaceId.value }
    }
    return { type: 'all', id: null }
})

function openBulkArchiveDialog(preset) {
    bulkArchiveDialog.value = { open: true, preset }
}

function handleBulkArchived({ count }) {
    toast.success(`Archived ${count} session${count > 1 ? 's' : ''}.`)
}
```

Vérifier que `computed` est dans les imports de `vue` en haut du fichier (devrait l'être : largement utilisé dans `ProjectView.vue`).

Puis, dans `handleSessionOptionsSelect`, ajouter le branchement en tout début de fonction (avant les cas existants) :

```js
function handleSessionOptionsSelect(event) {
    const value = event.detail.item.value
    if (value && value.startsWith('archive-older-')) {
        const preset = value.slice('archive-older-'.length)
        openBulkArchiveDialog(preset)
        return
    }
    // ... reste du code existant inchangé ...
}
```

- [ ] **Step 4 : Mount the dialog in the template**

Dans le `<template>` de `ProjectView.vue`, ajouter le composant à un endroit en dehors du dropdown (un dialog modal doit être un sibling, pas un enfant du dropdown). Un bon emplacement : juste avant le `</template>` de fermeture du composant, ou juste après le bloc `.sidebar-sessions`. Par exemple :

```html
        <BulkArchiveConfirmDialog
            v-model:open="bulkArchiveDialog.open"
            :preset="bulkArchiveDialog.preset"
            :scope="currentBulkArchiveScope"
            @archived="handleBulkArchived"
        />
```

(Utilise le `computed` défini au Step 3, pas un appel de fonction inline.)

- [ ] **Step 5 : Manual end-to-end test**

L'utilisateur :

1. Ouvre TwiCC dans le navigateur (frontend HMR a déjà rechargé).
2. Dans une vue projet : clique sur l'icône **sliders** (haut de la sidebar). Vérifie que **« Archive sessions older than… »** apparaît juste après « Show archived sessions ».
3. Hover dessus : un sous-menu s'ouvre avec les 8 durées.
4. Clique sur **7 days** : le dropdown se ferme, le dialog s'ouvre.
5. Vérifie :
   - Titre du dialog : « Archive sessions ».
   - Une ligne « Archive sessions older than [7 days ▾] » avec le select.
   - Section « Scope: » avec le `ProjectBadge` du projet courant.
   - Pendant le dry-run : `<wa-spinner>` + « Counting… ».
   - À la fin : « This will archive N session(s). » (ou « No sessions to archive… » si rien).
6. Change le select à 3 days : le compte se recalcule.
7. Clique « Cancel » : le dialog se ferme.
8. Rouvre, choisit une durée qui produit count = 0 : bouton « Archive » désactivé, message « No sessions to archive… ».
9. Rouvre, choisit une durée qui produit count > 0, clique « Archive N » : le dialog se ferme, le toast « Archived N session(s). » apparaît, la sidebar perd les sessions archivées en temps réel (via WS broadcast).
10. **Test workspace** : revenir en vue « All Projects », ouvrir un workspace, ouvrir le menu, lancer un archive — le dialog doit afficher l'icône colorée du workspace, son nom, et la liste plate des badges projets membres.
11. **Test all-projects** : en vue « All Projects » sans workspace actif, lancer un archive — le dialog doit afficher juste « All projects ».
12. **Test session active** : ouvrir une session avec un process actif, vérifier qu'elle n'apparaît pas dans le compte (même si son `mtime` est ancien).
13. **Test session pinned** : pinner une session ancienne, vérifier qu'elle n'apparaît pas dans le compte.

Si l'un des points 5-13 échoue, **ne pas committer** : investiguer et corriger d'abord.

- [ ] **Step 6 : Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "$(cat <<'EOF'
feat(archive): wire bulk-archive menu and dialog in sidebar

Adds "Archive sessions older than…" entry (with 8-duration submenu) to
the session list options dropdown. Opens BulkArchiveConfirmDialog scoped
to the current view (project / workspace / all). Shows a toast on success.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 : Update the archive-sessions tip

**Files :**
- Modify : `frontend/public/tips/archive-sessions.md`

- [ ] **Step 1 : Rewrite the tip body**

Remplacer le contenu actuel par :

```markdown
---
title: "Archive old sessions"
---

Use the archive icon in a session header, or a session's sidebar menu, to hide
old conversations from the sidebar. To archive many at once, open the
**session list options** menu (sliders icon at the top of the sidebar) and pick
a duration under **Archive sessions older than…**.

To view archived sessions again, open the **session list options** menu and
enable **Show archived sessions**.
```

Garder le front-matter (`title`) intact. Pas de nouveau champ.

- [ ] **Step 2 : Manual verification**

L'utilisateur peut vérifier le rendu en réinitialisant le tip dans Settings → Tips (« Reset all seen ») puis en attendant qu'il s'affiche, ou en regardant la liste des tips dans Settings.

- [ ] **Step 3 : Commit**

```bash
git add frontend/public/tips/archive-sessions.md
git commit -m "$(cat <<'EOF'
docs(tips): mention bulk archive in archive-sessions tip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Post-implementation checklist

Une fois tous les commits faits :

- [ ] Lire le `git log --oneline` des 6 derniers commits : doit donner une séquence claire et reviewable.
- [ ] Rappeler à l'utilisateur de redémarrer le backend si ce n'est pas déjà fait (pour Task 2).
- [ ] Suggérer un test rapide multi-scope (projet seul, workspace, all-projects) pour valider end-to-end.
- [ ] Notifier l'utilisateur que la feature est prête. Pas de push automatique.

## Liens

- **Spec :** [`docs/superpowers/specs/2026-05-18-bulk-archive-old-sessions-design.md`](../specs/2026-05-18-bulk-archive-old-sessions-design.md)
- **Memory references (subagent doit respecter) :**
  - `feedback_never_restart_servers.md` — ne jamais redémarrer le backend soi-même
  - `feedback_devctl_cd_worktree.md` — `cd <worktree>` avant chaque commande Bash si on est en worktree
  - `feedback_review_findings_must_be_fixed.md` — les findings d'une review doivent être corrigés, pas reportés
