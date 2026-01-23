# Spécifications POC - Claude Code Web UI

## Objectif

Valider l'architecture complète définie dans `architecture-decisions.md` avec un POC fonctionnel minimal :
- Backend Django ASGI avec Channels et WebSocket
- Synchronisation de fichiers JSONL via watchfiles
- Frontend Vue.js 3 réactif avec Vue Router (mode history)
- Communication temps réel entre tous les composants

---

## 1. Source de données

**Chemin** : `~/.claude/projects/`

**Structure** :
```
~/.claude/projects/
├── -home-twidi-dev-projet-a/
│   ├── uuid1.jsonl              # Session (à inclure)
│   ├── uuid2.jsonl              # Session (à inclure)
│   ├── agent-xxx.jsonl          # Agent (à exclure)
│   └── sessions-index.json      # Index (ignoré)
├── -home-twidi-dev-projet-b/
│   └── ...
```

**Règles** :
- Un **projet** = un sous-dossier de `~/.claude/projects/`
- Une **session** = un fichier `*.jsonl` à la racine du projet, **sauf** les `agent-*.jsonl`
- Un **session item** = une ligne d'un fichier session (JSON valide)

---

## 2. Modèles Django

### 2.1 Project

| Champ | Type | Description |
|-------|------|-------------|
| `id` | CharField(PK) | Nom du sous-dossier (ex: `-home-twidi-dev-xxx`) |
| `sessions_count` | PositiveIntegerField | Nombre de sessions (dénormalisé pour perf) |
| `mtime` | FloatField | Plus grand mtime parmi les sessions du projet |
| `archived` | BooleanField | `True` si le dossier n'existe plus sur disque |

### 2.2 Session

| Champ | Type | Description |
|-------|------|-------------|
| `id` | CharField(PK) | Nom du fichier sans extension (UUID) |
| `project` | ForeignKey(Project) | Référence au projet |
| `last_offset` | PositiveBigIntegerField | Position en bytes après la dernière ligne lue (permet de `seek()` directement puis lire jusqu'à EOF et split sur `\n`) |
| `last_line` | PositiveIntegerField | Numéro de la dernière ligne lue |
| `mtime` | FloatField | Timestamp de modification du fichier |
| `archived` | BooleanField | `True` si le fichier n'existe plus sur disque |

### 2.3 SessionItem

| Champ | Type | Description |
|-------|------|-------------|
| `id` | BigAutoField(PK) | Auto-increment |
| `session` | ForeignKey(Session) | Référence à la session |
| `line_num` | PositiveIntegerField | Numéro de ligne (1-indexed) |
| `content` | TextField | Contenu JSON brut de la ligne |

**Contrainte** : `UNIQUE(session, line_num)` — SQLite crée automatiquement un index sur cette contrainte, pas besoin d'index explicite supplémentaire.

---

## 3. Structure du projet

```
twicc-poc/
├── run.py                          # Point d'entrée
├── pyproject.toml                  # Dépendances uv
├── data.sqlite                     # Base de données (gitignore)
├── docs/
│   └── specs-poc.md                # Ce document
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.js                 # Bootstrap Vue + Pinia + Router
│       ├── router.js               # Vue Router config
│       ├── App.vue                 # Layout principal
│       ├── stores/
│       │   └── data.js             # Pinia store (projects, sessions, sessionItems)
│       ├── composables/
│       │   └── useWebSocket.js     # Connexion WebSocket (envoi/réception)
│       ├── views/
│       │   ├── HomeView.vue        # Liste des projets
│       │   ├── ProjectView.vue     # Détail projet + sidebar sessions
│       │   └── SessionView.vue     # Détail session (items)
│       └── components/
│           ├── ProjectList.vue
│           ├── SessionList.vue
│           └── SessionItem.vue
└── src/
    └── twicc_poc/
        ├── __init__.py             # main()
        ├── settings.py             # Config Django
        ├── urls.py                 # Routes HTTP
        ├── asgi.py                 # ASGI + WebSocket routing
        ├── views.py                # Vues API + SPA catch-all
        ├── watcher.py              # watchfiles async
        ├── sync.py                 # Logique de synchronisation
        └── core/
            ├── __init__.py
            ├── models.py           # Project, Session, SessionItem
            └── serializers.py      # Sérialisation JSON simple
```

---

## 4. Backend - Étapes d'implémentation

### 4.1 Initialisation du projet Python

```bash
cd /home/twidi/dev/twicc-poc
uv init --python 3.13
uv add django channels daphne watchfiles
```

**pyproject.toml** (généré/complété) :
```toml
[project]
name = "twicc-poc"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "django>=6.0",
    "channels>=4.0",
    "daphne>=4.0",
    "watchfiles>=0.21",
]

[project.scripts]
twicc_poc = "twicc_poc:main"
```

### 4.2 Configuration Django (settings.py)

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = "poc-insecure-key-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "channels",
    "twicc_poc.core",
]

MIDDLEWARE = []  # Aucun middleware nécessaire pour le POC

ROOT_URLCONF = "twicc_poc.urls"

ASGI_APPLICATION = "twicc_poc.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data.sqlite",
    }
}

# Static files
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "dist"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Source des données Claude
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
```

### 4.3 Modèles Django (core/models.py)

Définir les 3 modèles selon la section 2.

### 4.4 Migrations

```bash
uv run python -m django makemigrations core --settings=twicc_poc.settings
uv run python -m django migrate --settings=twicc_poc.settings
```

### 4.5 Commande de synchronisation (sync.py)

**Algorithme** :

1. **Scanner les projets** :
   - Lister les sous-dossiers de `CLAUDE_PROJECTS_DIR`
   - Créer les `Project` manquants
   - Marquer `archived=True` les projets dont le dossier n'existe plus

2. **Scanner les sessions par projet** :
   - Lister les `*.jsonl` sauf `agent-*.jsonl`
   - Créer les `Session` manquantes (offset=0, line=0)
   - Marquer `archived=True` les sessions dont le fichier n'existe plus
   - Mettre à jour `sessions_count` et `mtime` du projet

3. **Synchroniser les items par session** :
   - Comparer `mtime` du fichier vs stocké
   - Si différent : `seek(last_offset)`, lire les nouvelles lignes
   - Insérer les `SessionItem`
   - Mettre à jour `last_offset`, `last_line`, `mtime`

**Commande Django** : `python manage.py sync`

**Output console** : Affichage dynamique et informatif :
- Projet en cours avec progression `[x/n]`
- Barre de progression par projet (relative au nombre de sessions)
- Temps écoulé et estimation du temps restant

### 4.6 ASGI et WebSocket (asgi.py)

```python
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.urls import path

class UpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def broadcast(self, event):
        await self.send_json(event["data"])

websocket_urlpatterns = [
    path("ws/", UpdatesConsumer.as_asgi()),
]

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter(websocket_urlpatterns),
})
```

### 4.7 File Watcher (watcher.py)

**Note** : `awatch` est récursif par défaut, il surveille tous les sous-dossiers.

```python
from watchfiles import awatch, Change
from channels.layers import get_channel_layer
from pathlib import Path

async def start_watcher():
    channel_layer = get_channel_layer()
    projects_dir = Path(settings.CLAUDE_PROJECTS_DIR)

    async for changes in awatch(projects_dir):
        for change_type, path_str in changes:
            path = Path(path_str)

            # Gérer l'ajout de nouveaux dossiers projet (enfant direct de CLAUDE_PROJECTS_DIR)
            if path.is_dir() and path.parent == projects_dir:
                await sync_project_and_broadcast(path, change_type, channel_layer)
                continue

            if not path_str.endswith('.jsonl'):
                continue
            if '/agent-' in path_str:
                continue

            # Synchroniser et broadcaster
            await sync_and_broadcast(path, change_type, channel_layer)
```

### 4.8 Messages WebSocket

**Types de messages** :

```javascript
// Nouveau projet
{ "type": "project_added", "project": { "id": "...", "sessions_count": 0, "mtime": 0, "archived": false } }

// Projet modifié
{ "type": "project_updated", "project": { "id": "...", "sessions_count": 5, "mtime": 1234567890.0, "archived": false } }

// Nouvelle session
{ "type": "session_added", "session": { "id": "...", "project_id": "...", "last_line": 0, "mtime": 0, "archived": false } }

// Session modifiée (méta uniquement)
{ "type": "session_updated", "session": { "id": "...", "project_id": "...", "last_line": 42, "mtime": 1234567890.0, "archived": false } }

// Nouveaux items de session (content = JSON brut en string, décodage côté frontend)
{ "type": "session_items_added", "session_id": "...", "project_id": "...", "items": ["{ \"type\": \"user\", ... }", "{ \"type\": \"assistant\", ... }"] }
```

### 4.9 API HTTP (views.py)

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/projects/` | GET | Liste tous les projets |
| `/api/projects/<id>/` | GET | Détail d'un projet |
| `/api/projects/<id>/sessions/` | GET | Sessions d'un projet |
| `/api/projects/<id>/sessions/<session_id>/` | GET | Détail d'une session |
| `/api/projects/<id>/sessions/<session_id>/items/` | GET | Items d'une session |
| `/*` (catch-all) | GET | Sert `index.html` pour Vue Router |

### 4.10 URLs (urls.py)

```python
from django.urls import path, re_path
from . import views

urlpatterns = [
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),

    # Catch-all pour Vue Router (doit être en dernier)
    re_path(r"^(?!api/|static/|ws/).*$", views.spa_index),
]
```

### 4.11 Point d'entrée (run.py)

```python
#!/usr/bin/env python
import os
import sys
import asyncio

def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc_poc.settings")

    import django
    django.setup()

    # Migrations auto
    from django.core.management import call_command
    call_command("migrate", verbosity=0)

    # Sync initial
    from twicc_poc.sync import sync_all
    sync_all()

    # Lancer Daphne avec watcher
    from daphne.cli import CommandLineInterface
    sys.argv = ["daphne", "-b", "0.0.0.0", "-p", "8000", "twicc_poc.asgi:application"]
    CommandLineInterface().run()

if __name__ == "__main__":
    main()
```

**Intégration du watcher** : Le watcher doit être démarré comme tâche asyncio au sein de l'application ASGI (via lifespan ou signal ready).

---

## 5. Frontend - Étapes d'implémentation

### 5.1 Initialisation

```bash
cd /home/twidi/dev/twicc-poc
mkdir frontend && cd frontend
npm init -y
npm install vue@3 vue-router@4 pinia @vueuse/core @awesome.me/webawesome
npm install -D vite @vitejs/plugin-vue
```

### 5.2 Vite config (vite.config.js)

```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
    plugins: [
        vue({
            template: {
                compilerOptions: {
                    isCustomElement: (tag) => tag.startsWith('wa-')
                }
            }
        })
    ],
    build: {
        outDir: 'dist',
        emptyOutDir: true
    },
    server: {
        proxy: {
            '/api': 'http://localhost:8000',
            '/ws': { target: 'ws://localhost:8000', ws: true }
        }
    }
})
```

### 5.3 Web Awesome

Utilisation du thème "awesome" via npm (installé avec les dépendances).

**Import du thème** dans `main.js` :
```javascript
import '@awesome.me/webawesome/dist/styles/themes/awesome.css'
```

**Configuration de `index.html`** :
```html
<!DOCTYPE html>
<html lang="fr" class="wa-theme-awesome wa-palette-bright wa-brand-blue">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TWICC POC</title>
</head>
<body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
</body>
</html>
```

**Documentation Web Awesome** 
Une version "one file" quasi complète de la doc est disponible dans `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`
La documentation complete est aussi dans `/home/twidi/dev/webawesome/packages/webawesome/docs/docs/` (`usage.md` et `frameworks/vue.md`) 


### 5.4 Vue Router (router.js)

```javascript
import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import ProjectView from './views/ProjectView.vue'
import SessionView from './views/SessionView.vue'

const routes = [
    { path: '/', name: 'home', component: HomeView },
    { path: '/project/:projectId', name: 'project', component: ProjectView },
    { path: '/project/:projectId/session/:sessionId', name: 'session', component: SessionView },
]

export const router = createRouter({
    history: createWebHistory(),
    routes
})
```

### 5.5 Store Pinia (stores/data.js)

**Pinia** est la librairie officielle de gestion d'état pour Vue 3. La fonctionnalité clé est `$patch()` qui effectue un **deep merge récursif intelligent** : seules les propriétés modifiées déclenchent un re-render, les propriétés existantes non présentes dans le patch sont préservées.

```javascript
// frontend/src/stores/data.js

import { defineStore } from 'pinia'

export const useDataStore = defineStore('data', {
    state: () => ({
        projects: {},       // { id: { id, sessions_count, mtime, archived } }
        sessions: {},       // { id: { id, project_id, last_line, mtime, archived } }
        sessionItems: {}    // { sessionId: ["{ json string }", ...] }
    }),

    getters: {
        projectList: (state) => Object.values(state.projects),
        getProject: (state) => (id) => state.projects[id],
        getProjectSessions: (state) => (projectId) =>
            Object.values(state.sessions)
                .filter(s => s.project_id === projectId)
                .sort((a, b) => b.mtime - a.mtime),  // tri par mtime décroissant
        getSession: (state) => (id) => state.sessions[id],
        getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || []
    },

    actions: {
        // Projets
        addProject(project) {
            this.$patch({ projects: { [project.id]: project } })
        },
        updateProject(project) {
            // $patch fait un deep merge : seules les props modifiées triggent un re-render
            this.$patch({ projects: { [project.id]: project } })
        },

        // Sessions
        addSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
        },
        updateSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
        },

        // Session Items (array push nécessite la syntaxe fonction)
        addSessionItems(sessionId, newItems) {
            this.$patch((state) => {
                if (!state.sessionItems[sessionId]) {
                    state.sessionItems[sessionId] = []
                }
                state.sessionItems[sessionId].push(...newItems)
            })
        },

        // Chargement initial depuis l'API
        async loadProjects() {
            const res = await fetch('/api/projects/')
            const projects = await res.json()
            for (const p of projects) {
                this.projects[p.id] = p
            }
        },
        async loadSessions(projectId) {
            const res = await fetch(`/api/projects/${projectId}/sessions/`)
            const sessions = await res.json()
            for (const s of sessions) {
                this.sessions[s.id] = s
            }
        },
        async loadSessionItems(projectId, sessionId) {
            const res = await fetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
            const items = await res.json()
            this.sessionItems[sessionId] = items
        }
    }
})
```

### 5.6 Connexion WebSocket (composables/useWebSocket.js)

Une seule connexion WebSocket pour toute l'application. Gère la réception des messages du backend et expose `send()` pour l'envoi futur de messages vers le backend.

```javascript
// frontend/src/composables/useWebSocket.js

import { useWebSocket as useVueWebSocket } from '@vueuse/core'
import { useDataStore } from '../stores/data'

export function useWebSocket() {
    const store = useDataStore()

    const { status, send } = useVueWebSocket(`ws://${location.host}/ws/`, {
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
            case 'project_added':
                store.addProject(msg.project)
                break
            case 'project_updated':
                store.updateProject(msg.project)
                break
            case 'session_added':
                store.addSession(msg.session)
                break
            case 'session_updated':
                store.updateSession(msg.session)
                break
            case 'session_items_added':
                store.addSessionItems(msg.session_id, msg.items)
                break
        }
    }

    return { wsStatus: status, send }
}
```

### 5.7 Main Entry Point (main.js)

```javascript
// frontend/src/main.js

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { router } from './router'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
```

### 5.8 Vues

**HomeView.vue** :
- Affiche la liste des projets (id + sessions_count + mtime formaté en datetime local lisible)
- Clic → navigation vers `/project/:id`

**ProjectView.vue** :
- Layout : sidebar gauche + zone principale
- **Sidebar** :
  - En haut : `<wa-select>` avec le projet courant sélectionné, liste tous les projets, permet de changer de projet (navigation vers `/project/:newProjectId`)
  - En dessous : liste des sessions (id + last_line + mtime formaté en datetime local lisible)
  - Liste triée par mtime décroissant (session la plus récente en haut) — le tri est fait dans le getter Pinia `getSessionsForProject`
  - La session actuellement affichée est visuellement distinguée (background différent, bordure, ou style "active")
- Zone principale : message "Sélectionnez une session" ou `<router-view>` imbriqué
- Clic session → navigation vers `/project/:projectId/session/:sessionId`

**SessionView.vue** :
- **En-tête** : affiche le nom de la session (son id) + nombre de lignes (last_line) + mtime formaté en datetime local lisible
- Affiche la liste des items de session
- Chaque item : JSON formaté en arbre avec indentation/couleurs
- Séparateur visuel entre les items

### 5.9 Composant SessionItem.vue

- Reçoit `content` (string JSON brut)
- Parse et affiche en mode "tree view" :
  - Clés en couleur
  - Valeurs selon type (string, number, boolean, null)
  - Objets/arrays collapsibles (optionnel pour POC)
  - Indentation 2 espaces

---

## 6. Intégration Frontend/Backend

### 6.1 Build process

```bash
# Dans frontend/
npm run build    # → génère frontend/dist/

# Django collectstatic copie vers staticfiles/
uv run python -m django collectstatic --noinput
```

### 6.2 Servir le SPA

La vue `spa_index` sert `frontend/dist/index.html` pour toutes les routes non-API :

```python
from django.http import FileResponse
from django.conf import settings

def spa_index(request):
    index_path = settings.BASE_DIR / "frontend" / "dist" / "index.html"
    return FileResponse(open(index_path, "rb"), content_type="text/html")
```

### 6.3 Static files

Django sert les fichiers statiques depuis `frontend/dist/` via `STATICFILES_DIRS`.

---

## 7. Ordre d'implémentation

### Phase 1 : Setup Python
1. `uv init --python 3.13` + `uv add` dépendances
2. Créer structure `src/twicc_poc/`
3. `settings.py` minimal
4. `core/models.py` avec les 3 modèles
5. Générer et appliquer les migrations

### Phase 2 : Synchronisation
6. `sync.py` avec logique complète
7. Commande Django `sync` (optionnel, peut être appelé directement)
8. Tester la sync manuellement

### Phase 3 : API HTTP
9. `views.py` avec les endpoints API
10. `urls.py` avec routage
11. Tester les endpoints avec curl

### Phase 4 : WebSocket
12. `asgi.py` avec consumer WebSocket
13. `watcher.py` avec watchfiles
14. Intégrer watcher dans le lifecycle ASGI
15. Tester la réception de messages WS

### Phase 5 : Frontend Setup
16. Initialiser projet npm (avec pinia)
17. Configurer Vite + Vue
18. `router.js` avec les 3 routes
19. `stores/data.js` Pinia store
20. `composables/useWebSocket.js` connexion WS

### Phase 6 : Vues Frontend
21. `App.vue` layout de base
22. `HomeView.vue` liste projets
23. `ProjectView.vue` avec sidebar
24. `SessionView.vue` avec items
25. `SessionItem.vue` affichage JSON

### Phase 7 : Intégration
26. Build frontend
27. Vue catch-all Django
28. Test complet F5 sur chaque route
29. Test temps réel (modifier un fichier JSONL)

### Phase 8 : Point d'entrée
30. `run.py` avec bootstrap complet
31. Test `uv run ./run.py`

---

## 8. Tasks Tracking

**STATUS: COMPLETED** ✓

- [x] Phase 1 : Setup Python
- [x] Phase 2 : Synchronisation
- [x] Phase 3 : API HTTP
- [x] Phase 4 : WebSocket
- [x] Phase 5 : Frontend Setup
- [x] Phase 6 : Vues Frontend
- [x] Phase 7 : Intégration
- [x] Phase 8 : Point d'entrée
- [x] Review finale approfondie
- [x] Validation technique (lint + build)

### Follow-up Tasks (Batch 1)

- [x] Follow-up 1 : Ignorer les sessions vides (0 lignes)
- [x] Follow-up 2 : Argument --reset pour la commande sync
- [x] Follow-up 3 : Gestion des variables d'environnement (port configurable)
- [x] Review finale follow-up
- [x] Validation technique follow-up (lint + build)

### Follow-up Tasks (Batch 2)

- [x] Follow-up 4 : Ne pas supprimer les sessions existantes devenues vides (déjà conforme)
- [x] Follow-up 5 : Afficher des informations de progression au lancement (migrate, sync, etc.)
- [x] Review finale Batch 2
- [x] Validation technique Batch 2 (lint + build)

### Follow-up Tasks (Batch 3)

- [x] Follow-up 6 : Fix bug JSONDecodeError sur heartbeat WebSocket
- [x] Review finale Batch 3
- [x] Validation technique Batch 3 (lint + build)

---

## 12. Follow-up Specifications

### Follow-up 1 : Ignorer les sessions vides (0 lignes)

**Sync initial et commande sync** :
- Lors de la synchronisation, si un fichier session a 0 lignes, il doit être complètement ignoré
- Il ne doit pas être créé en base de données
- Il ne doit pas être compté dans `sessions_count` du projet
- Il ne doit pas influencer le `mtime` du projet

**Watcher temps réel** :
- Si un fichier est créé/modifié et qu'après lecture il a 0 lignes, l'ignorer complètement
- Ne pas créer de session en base
- Ne pas broadcaster de message WebSocket

### Follow-up 2 : Argument --reset pour la commande sync

La commande Django `python manage.py sync` doit accepter un argument `--reset` qui :
- Supprime toutes les données de la base (Project, Session, SessionItem)
- Refait une synchronisation complète depuis zéro

```bash
uv run python -m django sync --reset --settings=twicc_poc.settings
```

### Follow-up 3 : Variables d'environnement avec python-dotenv

**Dépendance** : Ajouter `python-dotenv` au projet

**Configuration** :
- Charger les variables d'environnement depuis un fichier `.env` à la racine du projet
- Port par défaut : `3500` (au lieu de 8000)
- Variable d'environnement : `TWICC_PORT` pour override le port

**Fichier .env exemple** :
```
TWICC_PORT=3500
```

**Modification de run.py** :
- Charger dotenv au démarrage
- Lire `TWICC_PORT` avec fallback sur `3500`
- Utiliser ce port pour Daphne

### Follow-up 4 : Ne pas supprimer les sessions existantes devenues vides

**Problème** : Une session existante en base (avec des items) ne doit PAS être supprimée si son fichier devient vide ou est vidé.

**Comportement attendu** :
- Lors du sync, si une session existe déjà en base et que son fichier a maintenant 0 lignes, la session reste en base (on ne la supprime pas, on ne la modifie pas)
- Seules les NOUVELLES sessions avec 0 lignes doivent être ignorées (pas créées)
- Une session existante garde ses données même si le fichier source est vidé

### Follow-up 5 : Afficher des informations de progression au lancement

**Modification de run.py** :
- Afficher des messages informatifs à chaque étape du démarrage
- Exemples de messages :
  - "Loading environment..." ou "Environment loaded"
  - "Running migrations..."
  - "Synchronizing data..." avec statistiques (X projects, Y sessions)
  - "Starting server on http://0.0.0.0:PORT..."

**Format suggéré** :
```
TWICC POC Starting...
✓ Environment loaded
✓ Migrations applied
✓ Data synchronized (8 projects, 614 sessions)
→ Server starting on http://0.0.0.0:3500
```

### Follow-up 6 : Fix bug JSONDecodeError sur heartbeat WebSocket

**Problème** : Le frontend envoie des messages heartbeat "ping" (texte brut) via WebSocket. Le consumer backend hérite de `AsyncJsonWebsocketConsumer` qui essaie de décoder tout message reçu comme du JSON, causant une erreur :

```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

**Cause racine** :
- Le frontend utilise `@vueuse/core` `useWebSocket` avec `heartbeat: { message: 'ping', interval: 30000 }`
- Le message "ping" est du texte brut, pas du JSON
- `AsyncJsonWebsocketConsumer.receive()` appelle `decode_json()` sur tout message reçu

**Solution** :
- Option A : Changer le consumer pour hériter de `AsyncWebsocketConsumer` (non-JSON) et gérer le décodage manuellement
- Option B : Modifier le heartbeat frontend pour envoyer du JSON valide (ex: `{"type": "ping"}`)
- Option C : Override `receive()` dans le consumer pour intercepter les messages non-JSON avant `decode_json()`

**Solution recommandée** : Option B - modifier le frontend pour envoyer `{"type": "ping"}` comme heartbeat, plus propre et cohérent avec le reste de l'API WebSocket.

---

## 9. Decisions made during implementation

### Phase 1 : Setup Python

1. **urls.py minimal cree** : Le fichier `urls.py` (avec `urlpatterns = []`) a ete cree des la Phase 1 car Django le requiert via `ROOT_URLCONF` pour executer les migrations, meme s'il est vide.

2. **Structure src/ avec setuptools** : Ajout de `[tool.setuptools.packages.find] where = ["src"]` dans pyproject.toml pour indiquer que les sources sont dans le repertoire `src/`.

3. **Repertoire frontend/dist cree** : Le repertoire `frontend/dist/` a ete cree (vide) car il est reference dans `STATICFILES_DIRS` du settings.py, evitant ainsi des warnings Django.

4. **Fichier main.py supprime** : Le fichier `main.py` genere par defaut par `uv init` a ete supprime car non necessaire (nous utilisons la structure src/).

### Phase 2 : Synchronisation

1. **Callbacks pour progression** : Le module `sync.py` utilise des callbacks optionnels (`on_project_start`, `on_project_done`, `on_session_progress`) pour decouple la logique de synchronisation de l'affichage, permettant une reutilisation facile (commande Django, run.py, ou appels programmatiques).

2. **Classe ProgressDisplay** : L'affichage console est encapsule dans une classe `ProgressDisplay` qui gere les barres de progression, le temps ecoule et l'ETA. Elle utilise les sequences ANSI `\r\033[K` pour effacer et reecrire les lignes dynamiquement.

3. **Bulk create avec ignore_conflicts** : Les `SessionItem` sont crees en batch via `bulk_create(ignore_conflicts=True)` pour gerer les cas ou une ligne existerait deja (protection contre les doublons).

4. **Synchronisation incrementale par mtime** : La comparaison du mtime permet de ne traiter que les fichiers modifies. Une re-synchronisation sans changement s'execute en moins d'une seconde meme avec plus de 600 sessions.

### Phase 3 : API HTTP

1. **Serializers simples** : Les serializers dans `core/serializers.py` sont des fonctions simples qui convertissent les modeles en dictionnaires. Pas d'utilisation de Django REST Framework pour garder le POC minimal.

2. **Session items retournes comme strings JSON** : La fonction `serialize_session_item` retourne directement le contenu JSON brut (string) plutot qu'un dictionnaire parse. Le parsing est fait cote frontend comme specifie dans la section 4.8 du document.

3. **Verification du projet pour les sessions** : Les endpoints `session_detail` et `session_items` verifient que la session appartient bien au projet specifie dans l'URL (`project_id`) pour garantir la coherence des donnees.

4. **Message d'erreur explicite pour SPA** : La vue `spa_index` retourne un message clair si le frontend n'est pas build, indiquant la commande a executer.

### Phase 4 : WebSocket

1. **LifespanApp pour le watcher** : Le watcher est demarre via le protocole ASGI lifespan. Une classe `LifespanApp` encapsule l'application principale et gere les evenements `lifespan.startup` et `lifespan.shutdown` pour demarrer et arreter la tache asyncio du watcher.

2. **Wrappers sync_to_async** : Les operations de base de donnees dans `watcher.py` utilisent des fonctions wrapper avec `sync_to_async` de asgiref pour executer les operations Django ORM (synchrones) dans le contexte asyncio du watcher.

3. **Reutilisation de sync_session_items** : La fonction `sync_session_items` de `sync.py` est reutilisee dans le watcher (via un wrapper async) pour eviter la duplication de la logique de synchronisation incrementale.

4. **Gestion des suppressions** : Le watcher detecte les suppressions de fichiers/dossiers (`Change.deleted`) et marque les projets/sessions comme `archived=True` au lieu de les supprimer, conformement au comportement defini dans `sync.py`.

5. **Filtrage des chemins** : Le watcher filtre les fichiers en deux etapes : d'abord par extension (`.jsonl`), puis par nom (exclusion des `agent-*.jsonl`), et verifie que le fichier est un enfant direct d'un dossier projet.

### Phase 5 : Frontend Setup

1. **package.json avec type module** : Ajout de `"type": "module"` dans package.json pour permettre l'utilisation des imports ES modules dans les fichiers de configuration Vite.

2. **Scripts npm standards** : Ajout des scripts `dev`, `build` et `preview` dans package.json conformement aux conventions Vite.

3. **Placeholders pour les vues** : Creation de composants Vue placeholders minimaux pour `HomeView.vue`, `ProjectView.vue` et `SessionView.vue` afin que le build Vite fonctionne (les imports du router doivent etre resolus). Ces composants seront implementes en Phase 6.

4. **App.vue minimal** : Creation d'un composant `App.vue` minimal contenant uniquement `<router-view />` comme point d'entree de l'application.

5. **Import CSS Web Awesome** : Le theme Web Awesome est importe en premier dans `main.js` via `import '@awesome.me/webawesome/dist/styles/themes/awesome.css'` avant les autres imports.

### Phase 6 : Vues Frontend

1. **Routes imbriquees pour SessionView** : Le router utilise des routes enfants pour `SessionView` a l'interieur de `ProjectView`. Cela permet d'afficher la session dans la zone principale tout en conservant la sidebar du projet. La route `project` a `component: null` pour le cas ou aucune session n'est selectionnee.

2. **Composants separes ProjectList et SessionList** : Les listes de projets et de sessions sont extraites en composants reutilisables qui emettent des evenements `@select` pour la navigation. Cela permet une meilleure separation des responsabilites.

3. **WebSocket initialise dans App.vue** : La connexion WebSocket est etablie une seule fois dans `App.vue` lors du montage de l'application. Les mises a jour sont automatiquement propagees au store Pinia.

4. **Chargement des donnees avec watch + immediate** : Les sessions et items sont charges via des watchers Vue avec `immediate: true` pour charger les donnees lors du montage initial et lors des changements de route.

5. **SessionItem avec composant recursif JsonNode** : L'affichage du JSON utilise un composant recursif defini dans le meme fichier via `export default` avec une propriete `components`. Cela permet d'afficher des structures JSON imbriquees avec collapse/expand.

6. **Import explicite des composants Web Awesome** : Chaque composant Web Awesome utilise (wa-card, wa-select, wa-option, wa-divider, wa-icon) est importe explicitement dans `main.js` pour s'assurer qu'il est enregistre comme custom element.

7. **Styles avec variables CSS Web Awesome** : Les composants utilisent les variables CSS de Web Awesome (`--wa-color-*`, `--wa-space-*`, `--wa-font-*`, etc.) pour une coherence visuelle avec le design system.

### Phase 7 : Integration

1. **Base path Vite pour static files** : Ajout de `base: '/static/'` dans `vite.config.js` pour que les assets generes soient references avec le prefixe `/static/` correspondant a `STATIC_URL` de Django.

2. **Route static files en mode DEBUG** : Ajout dans `urls.py` de la configuration `static()` pour servir les fichiers statiques depuis `frontend/dist/` en mode DEBUG. Django ne sert pas automatiquement les fichiers de `STATICFILES_DIRS` sans cette configuration explicite.

3. **Tests d'integration valides** : Tous les tests des etapes 26-28 de la section 7 passent :
   - Build frontend genere `frontend/dist/` avec `index.html`, CSS et JS
   - Vue `spa_index` sert correctement `index.html` pour la racine et les routes Vue Router
   - Fichiers statiques servis depuis `/static/assets/`
   - API endpoints fonctionnels
   - Catch-all Vue Router fonctionne pour `/project/*` et `/project/*/session/*`

### Phase 8 : Point d'entree

1. **Ajout du chemin src/ au sys.path** : Le fichier `run.py` est a la racine du projet, mais les modules sont dans `src/`. Ajout de `sys.path.insert(0, str(src_dir))` pour permettre l'import de `twicc_poc`.

2. **API Daphne CommandLineInterface** : La methode `run()` de Daphne requiert un argument `args` (liste des arguments CLI). Le code de la spec utilisant `sys.argv` ne fonctionne pas directement. Correction : `cli.run(["-b", "0.0.0.0", "-p", "8000", "twicc_poc.asgi:application"])`.

3. **Watcher deja integre** : Le watcher est demarre via le protocole lifespan dans `asgi.py` (classe `LifespanApp`), pas besoin de configuration supplementaire dans `run.py`.

### Follow-up 1 : Ignorer les sessions vides

1. **Indicateur special None** : La fonction `sync_session_items()` retourne desormais `int | None`. Elle retourne `None` quand le fichier a 0 lignes (fichier vide ou contenant uniquement des espaces/lignes vides). Cela permet a l'appelant de distinguer "0 nouvelles lignes ajoutees" de "fichier completement vide".

2. **Creation conditionnelle des sessions** : Dans `sync_project()`, les sessions ne sont creees en base que si le fichier a du contenu. Un objet `Session` temporaire (non sauvegarde) est utilise pour tester le contenu via `sync_session_items()`. Si le retour est `None`, la session n'est pas creee.

3. **Comptage des sessions non-vides uniquement** : Le champ `sessions_count` du projet ne compte que les sessions ayant `last_line > 0`. Les sessions vides ne sont pas comptabilisees.

4. **mtime du projet** : Le `mtime` du projet n'est calcule qu'a partir des sessions non-vides (celles avec `last_line > 0`).

5. **Watcher : verification avant creation** : Dans `watcher.py`, avant de creer une nouvelle session, le watcher verifie si le fichier a du contenu. Si `sync_session_items()` retourne `None`, aucune session n'est creee et aucun message WebSocket n'est envoye.

6. **update_project_metadata filtrage** : La fonction `update_project_metadata()` dans `watcher.py` filtre les sessions avec `last_line__gt=0` pour ne compter que les sessions non-vides.

### Follow-up 2 : Argument --reset pour la commande sync

1. **Suppression par cascade** : Seule la suppression de `Project.objects.all()` est necessaire car les modeles `Session` et `SessionItem` ont `on_delete=models.CASCADE`. Django gere automatiquement la suppression en cascade des sessions et items associes.

2. **Argument --reset avec action store_true** : L'argument est implemente avec `action="store_true"` pour permettre un usage simple sans valeur (`--reset` au lieu de `--reset=True`).

### Follow-up 3 : Variables d'environnement avec python-dotenv

1. **Chargement dotenv au niveau module** : `load_dotenv()` est appele au debut de `run.py`, avant le `django.setup()`, pour que les variables d'environnement soient disponibles des le demarrage de l'application.

2. **Port par defaut 3500** : Le port par defaut est passe de 8000 a 3500 via `os.environ.get("TWICC_PORT", "3500")`.

3. **Fichier .env.example** : Un fichier `.env.example` est cree a la racine du projet comme modele pour les utilisateurs.

4. **Fichier .env dans .gitignore** : Le fichier `.env` est ajoute au `.gitignore` pour eviter de committer des configurations locales ou sensibles.

### Follow-up 5 : Afficher des informations de progression au lancement

1. **Comptage des projets et sessions** : Les statistiques affichees (nombre de projets et sessions) sont obtenues via des requetes directes sur les modeles apres la synchronisation, en filtrant sur `archived=False` pour ne compter que les entites actives.

2. **Format de sortie** : Le format suit exactement la spec avec les caracteres Unicode (✓ pour les etapes completees, → pour le demarrage du serveur).

3. **Port dynamique dans le message** : Le message du serveur utilise la variable `port` deja validee pour afficher l'URL complete avec le bon port.

### Follow-up 6 : Fix bug JSONDecodeError sur heartbeat WebSocket

1. **Solution choisie : Option B (heartbeat JSON)** : Le frontend a ete modifie pour envoyer le heartbeat en JSON valide (`{"type": "ping"}`) au lieu de texte brut (`ping`). Cette approche est plus propre et coherente avec le reste de l'API WebSocket qui utilise exclusivement du JSON.

---

## 10. Criteres de validation

Le POC est validé si :

1. **`uv run ./run.py`** démarre l'application sans erreur
2. **Page d'accueil** (`/`) affiche la liste des projets
3. **Page projet** (`/project/:id`) affiche les sessions en sidebar
4. **Page session** (`/project/:id/session/:id`) affiche les items JSON
5. **F5 sur chaque URL** recharge correctement la page sans 404
6. **Modification d'un fichier JSONL** → mise à jour automatique de l'UI sans refresh
7. **Création d'un nouveau fichier JSONL** → apparition de la nouvelle session en temps réel
8. **Création d'un nouveau dossier projet** → apparition du projet en temps réel

---

## 11. Hors scope (pour ce POC)

- Authentification
- Gestion des erreurs sophistiquée
- Tests automatisés
- Linting/formatting
- Suppression physique de projets/sessions (on archive seulement)
- Pagination des items
- Recherche/filtrage
- Thème sombre
- Responsive mobile
