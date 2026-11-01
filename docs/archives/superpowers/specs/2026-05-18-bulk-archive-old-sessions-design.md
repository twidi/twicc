# Bulk archive old sessions — design

**Date :** 2026-05-18
**Statut :** Draft
**Scope :** Backend + Frontend

Document de cadrage pour ajouter une action manuelle « **Archive sessions older
than …** » dans le dropdown *session list options* de la sidebar. L'utilisateur
choisit une période parmi 8 presets, confirme dans un dialog qui affiche le
scope (projet / workspace / all-projects), le compte exact et un sélecteur de
durée modifiable à la volée ; un seul appel backend archive en masse les
sessions correspondantes, hors sessions épinglées et hors sessions avec
processus actif.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Un nouvel item **« Archive sessions older than… »** dans le dropdown des
  *session list options* de la sidebar (`ProjectView.vue:1236`), positionné
  juste après *Show archived sessions* pour regrouper les items liés à
  l'archivage.
- Cet item ouvre un sous-menu (`slot="submenu"` de `<wa-dropdown-item>`)
  proposant 8 durées : **3d, 7d, 10d, 20d, 30d, 2m, 3m, 6m** — mêmes presets
  que le filtre temporel du full-text search (`SearchOverlay.vue:594-602`).
- Le clic sur une durée ouvre un **dialog de confirmation**. Le dialog :
  - Affiche le **scope** affecté en respectant les conventions visuelles
    existantes : projet = `<ProjectBadge>` (point coloré + nom), workspace =
    `<wa-icon name="layer-group" :style="{ color }">` + nom + liste plate des
    projets membres (pattern `ProjectDetailNavList.vue`).
  - Affiche un **compte exact** des sessions qui seront archivées
    (`dry_run` côté backend).
  - Contient un **`<wa-select>` de durée** présélectionné sur celle cliquée,
    permettant de changer à la volée — chaque changement relance un dry-run.
  - Bouton « Archive N » (avec le compte) et « Cancel ». Si compte = 0,
    bouton désactivé + message « No sessions to archive ».
- Un **endpoint backend bulk** `POST /api/sessions/bulk-archive/` qui applique
  l'archivage côté DB en un seul `UPDATE` SQL.
- Un **broadcast WebSocket unique** `sessions_bulk_archived` portant la liste
  des IDs concernés (et non N broadcasts `session_updated` distincts — l'ordre
  de grandeur peut atteindre plusieurs centaines / milliers).
- La **réindexation Tantivy** est lancée en arrière-plan
  (`asyncio.create_task`) après le broadcast — elle ne bloque pas la réponse
  HTTP.
- Toast après succès : « Archived N sessions. ».
- Le **tip existant** `frontend/public/tips/archive-sessions.md` est enrichi
  pour mentionner aussi le bulk archive.

### 0.2 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas d'auto-archive** (tâche périodique globale). Pourrait être ajouté plus
  tard dans Settings → Sessions à côté de *Auto-unpin on archive*, mais c'est
  hors scope ici. Le besoin principal (premier import massif) est couvert par
  l'action manuelle.
- **Pas d'undo**. L'opération est réversible session par session via la
  désarchivage existant (icône / kebab menu). Pas de bouton « Undo » dans le
  toast.
- **Pas de durée custom**. Uniquement les 8 presets, mêmes que le full-text
  search.
- **Pas de toggle « exclure les non-lues »**. Le critère d'âge suffit ;
  ajouter une exclusion supplémentaire rendrait le comportement moins
  prévisible.
- **Pas de toast/dialog de progression**. La réponse HTTP est rapide (un seul
  UPDATE SQL, la reindex est asynchrone). Pas de barre de progression.
- **Pas de comportement spécial pour `autoUnpinOnArchive`** dans le bulk : les
  sessions épinglées sont filtrées hors lot, le setting n'a aucun effet.
  L'archivage unitaire continue de respecter le setting comme aujourd'hui.
- **Pas de migration DB**, **pas de nouveau champ**.
- **Pas de scope global** depuis ce menu. Le menu vit dans la sidebar
  contextuelle, donc le scope suit la vue (projet courant / workspace courant
  / all-projects). Une action « toujours globale » serait dans Settings, hors
  scope.

### 0.3 Vocabulaire

| Terme | Définition |
|-------|------------|
| **Bulk archive** | L'action d'archiver plusieurs sessions en un appel — sujet de ce spec. |
| **Scope** | Le périmètre de sessions affectées par un bulk archive : `project` (un projet), `workspace` (un workspace = N projets), `all` (tous les projets). Déduit de la vue active côté frontend. |
| **Preset** | Une chaîne de durée parmi `3d, 7d, 10d, 20d, 30d, 2m, 3m, 6m`. Convertie en timestamp ISO côté frontend, puis en epoch côté backend. |
| **Dry run** | Appel à l'endpoint avec `dry_run: true` qui retourne uniquement le compte sans modifier la DB. Utilisé par le dialog pour afficher le compte avant confirmation, et recalculer à chaque changement de preset. |
| **Active session** | Une session avec un agent live (`get_agent_manager_registry().get_active_agents()` retourne un `AgentInfo` non-DEAD). Exclue du bulk. |

---

## 1. Filtres et exclusions backend

Une session est éligible au bulk archive si et seulement si **toutes** les
conditions suivantes sont vraies :

| Critère | Filtre ORM |
|---------|------------|
| Type session (pas un subagent) | `type=SessionType.SESSION` |
| Visible (a au moins un message utilisateur) | `user_message_count__gt=0` |
| Visible (a une date de création) | `created_at__isnull=False` |
| Pas déjà archivée | `archived=False` |
| Non épinglée | `pinned__isnull=True` |
| Activité antérieure au seuil | `mtime__lt=<epoch>` |
| Pas de process actif | `.exclude(id__in=<active_session_ids>)` |
| Dans le scope demandé | voir tableau §2.2 |

Les trois premiers critères (`type`, `user_message_count`, `created_at`)
correspondent à la définition de **session visible** appliquée partout
ailleurs dans le projet : `update_project_metadata` (`projects.py:280`),
la vue `all_sessions` (`views.py:66`), et l'index `idx_session_visible`
(`models.py:374`). On respecte cette même définition pour ne pas archiver
des sessions invisibles dans la sidebar (cohérent avec l'attente
« archive ce que je vois »).

### 1.1 Pourquoi `mtime` et pas `last_updated_at`

Le frontend trie les sessions de la sidebar par **`mtime`** descending
(`sessionSortComparator` dans `frontend/src/stores/data.js:147`). C'est ce que
l'utilisateur voit visuellement comme « date » d'une session. Pour que le
bulk archive corresponde à son intuition (« archive les sessions que je vois
en bas de la liste »), on filtre sur le même champ.

`mtime` est un `FloatField` (unix epoch, `models.py:276`), mis à jour par le
watcher à chaque changement du fichier JSONL.

### 1.2 Pourquoi exclure les sessions actives

Une session avec un agent live est implicitement « en cours d'utilisation »,
même si son `mtime` est ancien (rare mais possible : long process de fond
sans nouvelle activité depuis 8 jours). L'archiver tuerait le process et le
tmux associé (cf. `views.py:546-549`), ce qui serait surprenant en bulk.
L'archivage unitaire continue de tuer le process si l'utilisateur archive
explicitement — c'est une décision consciente sur une session précise.

### 1.3 Pourquoi exclure les pinned

Cohérent avec la sémantique du pin : marqueur de visibilité que l'utilisateur
veut préserver. En cas de race (un pin manuel pendant un bulk archive),
l'utilisateur peut désarchiver d'un click.

### 1.4 Subagents

Pas concernés directement : le filtre exclut tout ce qui a un
`parent_session_id`. Les subagents suivent leur parent — archiver le parent
les masque de facto, c'est l'existant.

---

## 2. Endpoint backend

### 2.1 Route

```
POST /api/sessions/bulk-archive/
```

Nouvelle entrée dans `src/twicc/urls.py`, à insérer dans la zone « API
endpoints » (vers `views.all_sessions`, `views.search_sessions`).

### 2.2 Payload

```json
{
  "older_than": "2026-05-11T00:00:00Z",
  "scope": "project" | "workspace" | "all",
  "project_id": "...",       // requis si scope=project, sinon ignoré
  "workspace_id": "...",     // requis si scope=workspace, sinon ignoré
  "dry_run": false           // optional, défaut false
}
```

Filtres appliqués selon le scope :

| `scope` | Filtre supplémentaire |
|---------|----------------------|
| `project` | `.filter(project_id=<id>)` |
| `workspace` | Lecture de `workspaces.json` via `read_workspaces()`, récupération du `projectIds` du workspace cible, puis `.filter(project_id__in=<ids>)`. 404 si workspace inconnu. |
| `all` | Pas de filtre supplémentaire. |

### 2.3 Réponse

Que ce soit en dry-run ou non, la réponse ne contient que le compte :

```json
{ "count": 47 }
```

Le frontend récupère la liste des IDs archivés via le broadcast WS
(`sessions_bulk_archived`, §3.1), pas via la réponse HTTP — pas besoin de
les renvoyer deux fois.

### 2.4 Implémentation

Vue **sync** (cohérent avec toutes les autres vues du projet). Le post-work
non bloquant (réindexation Tantivy + cleanup tmux) est lancé dans un thread
daemon pour ne pas bloquer la réponse HTTP. `channel_layer.group_send` est
invoqué via `async_to_sync`, exactement comme le PATCH unitaire actuel
(`views.py:558`).

Pseudo-code :

```python
def bulk_archive_sessions(request):
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

    # ── Validation ─────────────────────────────────────────────
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

    # ── Construction du queryset ───────────────────────────────
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
    # scope_type == "all": pas de filtre supplémentaire

    # ── Dry run : juste un count ───────────────────────────────
    if dry_run:
        return JsonResponse({"count": qs.count()})

    # ── Bulk update ────────────────────────────────────────────
    # 1. Capture des IDs AVANT l'UPDATE (sinon le queryset est vide après).
    #    Re-check des active_ids juste avant le commit pour fermer la fenêtre
    #    de race où une session est devenue active depuis le premier snapshot.
    ids = set(qs.values_list("id", flat=True))
    active_ids_now = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }
    ids -= active_ids_now

    # 2. UPDATE direct (un seul SQL).
    Session.objects.filter(id__in=ids).update(archived=True)

    # 3. Broadcast unique.
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)("updates", {
        "type": "broadcast",
        "data": {
            "type": "sessions_bulk_archived",
            "session_ids": list(ids),
        },
    })

    # 4. Post-work non bloquant : reindex Tantivy + cleanup tmux orphelins.
    #    Lancé dans un thread daemon. Tantivy est déjà appelé depuis un thread
    #    par le watcher (`sessions_watcher.py:620` via `asyncio.to_thread`).
    if ids:
        ids_snapshot = list(ids)  # capture par valeur pour le closure du thread
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

Notes d'implémentation :

- **Pourquoi `kill_all_tmux_terminals` ?** Le PATCH unitaire le fait
  inconditionnellement (`views.py:537`). Une session inactive peut avoir un
  tmux orphelin (ouvert manuellement par l'utilisateur, jamais utilisé pour un
  agent). On garde le même comportement en bulk pour la cohérence.
- **Pourquoi pas `kill_agent` ?** Le filtre `.exclude(id__in=active_ids)`
  garantit qu'aucune session archivée n'a d'agent actif (re-check juste avant
  le commit). Donc inutile.
- **Thread daemon et Tantivy** : `search.reindex_session` est déjà appelé via
  `asyncio.to_thread` dans le watcher (`sessions_watcher.py:620`), donc thread-safe.
  Les exceptions sont avalées par session pour ne pas tuer le thread entier —
  cohérent avec le `except Exception: pass` du PATCH (`views.py:527, 540`).
- **Reindex séquentiel** : la boucle traite les IDs un par un. Pour ~1000
  sessions × ~10ms par reindex, cela représente environ 10 secondes de travail
  en arrière-plan. Acceptable (le frontend est déjà à jour via le broadcast).
- **TOCTOU sur `active_ids`** : la fenêtre est fermée par le re-check juste
  avant le commit. Il reste un micro-intervalle entre le re-check et l'UPDATE
  SQL où une session pourrait devenir active. En pratique négligeable, et
  réversible (l'utilisateur peut désarchiver d'un click).
- **`read_workspaces()` sans le lock async** : c'est une lecture pure
  (`orjson.loads` sur le fichier entier). Le lock `_workspaces_lock` de
  `workspaces.py:31` ne protège que les cycles read-modify-write. Lecture
  seule sans lock = OK.
- **CSRF** : le projet n'a pas `CsrfViewMiddleware` (cf. `settings.py:108-111`).
  Aucun token requis pour ce POST, comme pour les autres endpoints.
- **Sync/async des composants invoqués** : `get_agent_manager_registry().get_active_agents()`
  est synchrone, itère une dict en mémoire (`base_manager.py`). Sûr à appeler
  depuis une vue sync.

---

## 3. WebSocket

### 3.1 Nouveau message

Émis une seule fois par le bulk archive :

```json
{
  "type": "sessions_bulk_archived",
  "session_ids": ["sess-1", "sess-2", "..."]
}
```

### 3.2 Pourquoi un seul message et pas N `session_updated`

Le PATCH unitaire actuel émet un `session_updated` par session. Pour le bulk,
ça ne scale pas : un utilisateur avec 1000-2000 sessions peut archiver
plusieurs centaines d'entrées en un coup, voire le millier. Émettre 1000
messages WS pour une opération atomique est gaspilleur et peut saturer le
canal pendant plusieurs secondes côté frontend (déserialisation, dispatch
Pinia, etc.).

Un seul message porte la liste — le handler frontend itère localement.

### 3.3 Handler frontend

Dans `frontend/src/composables/useWebSocket.js` (ou wherever le switch sur
`message.type` vit) :

```js
case 'sessions_bulk_archived':
    store.applyBulkArchiveFromBroadcast(message.session_ids)
    break
```

Avec dans `data.js` :

```js
applyBulkArchiveFromBroadcast(sessionIds) {
    for (const sid of sessionIds) {
        const session = this.sessions[sid]
        if (session) {
            // Mark archived in-place. Pas de gestion d'auto-unpin : les
            // sessions pinned étaient filtrées hors-lot côté backend.
            session.archived = true
        }
        // Retirer du MRU pour éviter que la nav par MRU pointe vers une
        // session désormais archivée. Le PATCH unitaire fait la même chose
        // (data.js:3193).
        this.removeMruSession(sid)
    }
    // Trigger any required refresh of derived state (lists, counts, …).
},
```

**Important — ne pas créer de boucle backend** : ce handler **ne doit pas**
réutiliser `setSessionArchived` du store, qui ferait un PATCH HTTP par
session. Le backend a déjà archivé les sessions ; le rôle du handler est
**uniquement** de :

1. mettre à jour l'état local Pinia (`session.archived = true`),
2. retirer du MRU,
3. laisser Vue re-render les dérivées (listes filtrées, compteurs, etc.).

**Aucun appel HTTP**, **aucune modification de pin** (les sessions étaient
filtrées pinned-free côté backend), **aucun broadcast retour**. La méthode
est purement locale + propagation Vue.

---

## 4. Frontend : dropdown

### 4.1 Modification de `ProjectView.vue`

Dans le dropdown existant (`ProjectView.vue:1236-1273`), ajout d'un nouvel
item juste après *Show archived sessions* :

```html
<wa-dropdown-item type="checkbox" value="show-archived" :checked="showArchivedSessions">
  Show archived sessions
</wa-dropdown-item>

<!-- ↓ Nouveau ↓ -->
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
<!-- ↑ Nouveau ↑ -->

<wa-dropdown-item type="checkbox" value="compact-view" :checked="compactView">
  Compact view
</wa-dropdown-item>
…
```

### 4.2 Comportement de `wa-select`

Documentation Web Awesome : *« Dropdown items that have a submenu will not
dispatch the `wa-select` event. However, items inside the submenu will. »*

L'item parent `archive-older` ne déclenche donc rien ; seuls les sous-items
(value `archive-older-{preset}`) déclenchent `wa-select`. Le handler existant
`handleSessionOptionsSelect` reçoit ces valeurs et branche :

```js
function handleSessionOptionsSelect(event) {
    const value = event.detail.item.value
    if (value.startsWith('archive-older-')) {
        const preset = value.slice('archive-older-'.length)  // '3d' | '7d' | …
        openBulkArchiveDialog(preset)
        return
    }
    // ... cas existants : show-archived, compact-view, show-active ...
}
```

### 4.3 Icône

`name="box-archive"` — l'icône standard du projet pour l'archivage, utilisée
dans `SessionHeader.vue:402` (icône d'archive d'une session), dans
`SessionListItem.vue:540` (menu kebab), `ProjectCard.vue:97` et
`WorkspaceCard.vue:129`. On reprend la même icône pour cohérence visuelle.

### 4.4 Première utilisation du slot `submenu` dans le projet

Le pattern `<wa-dropdown-item slot="submenu">` est valide d'après la doc Web
Awesome 3 (`llms.txt:930`) et nativement supporté par le composant. Mais une
recherche dans le repo confirme qu'aucun composant Vue actuel ne l'utilise.
Cette implémentation sera la première — prévoir une attention particulière
à la vérification visuelle (rendu du chevron sur l'item parent, navigation
clavier, fermeture du sous-menu sur sélection).

---

## 5. Frontend : dialog de confirmation

### 5.1 Nouveau composant

`frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue`.

Suit le pattern `ProjectEditDialog.vue` listé dans `CLAUDE.md` :

- `<form id="..." @submit.prevent="handleConfirm">` à l'intérieur du
  `<wa-dialog>`.
- Bouton submit hors `<form>` (slot footer du dialog), connecté via
  `setAttribute('form', '...')` après mount.
- Focus management : `@wa-after-show` pour mettre le focus sur le
  `<wa-select>` de durée.
- Largeur : `--width: min(520px, calc(100vw - 2rem))`.

### 5.2 Props et événements

```
props:
  open              : Boolean (v-model:open)
  preset            : String — preset initial (e.g. '7d')
  scope             : Object — { type: 'project'|'workspace'|'all', id: '...' | null }

emits:
  update:open       : Boolean
  archived          : { count: Number }   — émis sur succès
```

(Le composant n'émet que le compte ; la liste des IDs n'est pas renvoyée par
l'endpoint et n'est donc pas disponible côté frontend HTTP. Si un consommateur
en a besoin, il les obtient via le broadcast WS `sessions_bulk_archived`.)

### 5.3 Layout

```
┌──────────────────────────────────────────────┐
│ Archive sessions older than [7 days  ▾]   ×  │   ← le wa-select dans le titre
├──────────────────────────────────────────────┤
│                                              │
│  Scope:                                      │
│    [• My Project Name]                       │   ← cas projet
│                                              │
│  This will archive 47 sessions.              │   ← compte (ou spinner si dry-run en cours)
│                                              │
│  Sessions that are pinned or have an         │
│  active process are excluded.                │
│                                              │
├──────────────────────────────────────────────┤
│                       [Cancel] [Archive 47]  │
└──────────────────────────────────────────────┘
```

Note : le `<wa-select>` de durée peut être placé dans le body plutôt que dans
le titre si le rendu est moins lisible — arbitrage à faire à
l'implémentation.

### 5.4 Affichage du scope

| Scope | Rendu |
|-------|-------|
| `project` | `<ProjectBadge :project-id="scope.id" />` |
| `workspace` | `<wa-icon name="layer-group" auto-width :style="{ color: ws.color }"></wa-icon> {{ ws.name }}`, suivi sur une ligne en `display: flex; flex-wrap: wrap; gap: var(--wa-space-xs);` de tous les `<ProjectBadge>` du workspace. Pattern visuel inspiré de `ProjectDetailNavList.vue:157-167`. |
| `all` | Texte « All projects » seul. Pas de liste de projets ni de workspaces — l'utilisateur sait ce que ça veut dire, et lister 50+ projets surchargerait le dialog. |

### 5.5 Flow

1. Ouverture du dialog avec `preset` initial.
2. Au mount + à chaque changement du `<wa-select>` de durée :
   - Annulation de tout dry-run en cours (`AbortController`).
   - Nouvel appel `POST /api/sessions/bulk-archive/` avec `dry_run: true`,
     `older_than: presetToDate(currentPreset)`, `scope: ...`.
   - Pendant l'attente : `<wa-spinner>` à la place du compte. Le bouton
     « Archive N » est désactivé.
   - À la réponse : mise à jour du compte affiché et de la valeur dans le
     label du bouton. Si compte = 0, bouton désactivé + message « No
     sessions to archive ».
3. Clic « Archive N » : appel non-dry-run, désactivation du bouton +
   spinner pendant l'attente.
4. À la résolution réussie : `emit('archived', { count, ids })`, fermeture
   du dialog. Le toast est déclenché par le parent (le composant n'a pas à
   connaître le système de toast).
5. À la résolution en erreur : `<wa-callout variant="danger">` dans le body
   avec le message d'erreur, le bouton devient « Close ».

### 5.6 Source de vérité du preset courant

`currentPreset` est une ref locale au composant, initialisée depuis la prop
`preset`. Changer le `<wa-select>` modifie `currentPreset`, ne touche pas la
prop. Si l'utilisateur ferme et rouvre le dialog avec un autre preset, la
ref se ré-initialise.

---

## 6. Frontend : store action

Dans `frontend/src/stores/data.js`, nouvelle action :

```js
async bulkArchiveSessions({ olderThan, scope, dryRun = false, signal = null }) {
    const body = {
        older_than: olderThan,
        scope: scope.type,
        ...(scope.type === 'project' && { project_id: scope.id }),
        ...(scope.type === 'workspace' && { workspace_id: scope.id }),
        dry_run: dryRun,
    }
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
}
```

Le `signal: AbortController.signal` permet au dialog d'annuler un dry-run en
cours quand l'utilisateur change rapidement de preset.

L'action ne modifie pas le store local — c'est le handler WebSocket
(§3.3) qui applique les changements quand le broadcast arrive. Cela garde
une source de vérité unique (le backend) et évite les divergences si la
requête réussit côté backend mais échoue côté frontend (timeout, etc.).

---

## 7. Extraction de `presetToDate`

Actuellement `presetToDate` vit dans `SearchOverlay.vue:150-164`. À extraire
dans un util partagé :

`frontend/src/utils/datePresets.js`

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

`SearchOverlay.vue` est mis à jour pour importer depuis cet util.

---

## 8. Détection du scope côté frontend

Dans `ProjectView.vue`, le scope du bulk archive se déduit du router :

```js
function getCurrentScope() {
    if (!isAllProjectsMode.value) {
        return { type: 'project', id: projectId.value }
    }
    if (activeWorkspaceId.value) {
        return { type: 'workspace', id: activeWorkspaceId.value }
    }
    return { type: 'all', id: null }
}
```

Les helpers `isAllProjectsMode`, `activeWorkspaceId`, `projectId` existent
déjà (`ProjectView.vue:301-313`).

---

## 9. Toast de succès

À la résolution réussie de l'appel non-dry-run, le parent (probablement
`ProjectView.vue`) émet un toast via `useToast()` du projet
(`frontend/src/composables/useToast.js`) :

```js
useToast().success(`Archived ${count} sessions.`)
```

---

## 10. Mise à jour du tip

`frontend/public/tips/archive-sessions.md` actuel mentionne uniquement
l'archivage unitaire. Ajouter une phrase sur le bulk archive :

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

---

## 11. Récapitulatif des fichiers touchés

### 11.1 À créer

| Fichier | Rôle |
|---------|------|
| `frontend/src/utils/datePresets.js` | Util partagé : liste des presets + `presetToDate`. |
| `frontend/src/components/sidebar/BulkArchiveConfirmDialog.vue` | Dialog de confirmation. |

### 11.2 À modifier

| Fichier | Changements |
|---------|-------------|
| `src/twicc/views.py` | Nouvelle vue `async def bulk_archive_sessions(request)`. |
| `src/twicc/urls.py` | Nouvelle route `path("api/sessions/bulk-archive/", views.bulk_archive_sessions)`. |
| `frontend/src/components/app/SearchOverlay.vue` | Import de `presetToDate` depuis l'util partagé (suppression de la copie locale). |
| `frontend/src/views/ProjectView.vue` | Item + sous-menu dans le dropdown des session list options ; handler `archive-older-*` ; instanciation du dialog ; gestion du toast. |
| `frontend/src/stores/data.js` | Action `bulkArchiveSessions({...})` ; handler `applyBulkArchiveFromBroadcast(ids)`. |
| `frontend/src/composables/useWebSocket.js` (ou équivalent) | Switch case `sessions_bulk_archived` qui appelle l'handler du store. |
| `frontend/public/tips/archive-sessions.md` | Mention du bulk archive (+ une phrase). |

### 11.3 Volontairement non touché

- Pas de **migration** Django (aucun nouveau champ DB).
- Pas de **modification de `Session`** (filtre calculé à la volée).
- Pas de **nouveau setting** dans `synced_settings.py` ou `settings.js` (pas
  d'auto-archive global, pas de toggle).
- Pas de **modification du PATCH unitaire** (`session_detail` dans
  `views.py`).
