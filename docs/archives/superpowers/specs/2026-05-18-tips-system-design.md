# Tips system — design

**Date :** 2026-05-18
**Statut :** Draft
**Scope :** Backend + Frontend
**Worktree :** `feature/tips-system`

Document de cadrage pour ajouter un système de **tips** (astuces contextuelles)
affichés en toast de temps en temps, avec un cooldown post-fermeture,
multi-device synced, avec contraintes par tip (plateforme, OS, providers
activés) et liste consultable depuis les Settings.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Des fichiers markdown (un par tip) dans `frontend/public/tips/`, avec
  front-matter YAML pour les méta-données (titre, contraintes).
- Affichage automatique d'un tip random « de temps en temps », sous forme de
  toast Notivue **sticky**.
- Le toast permet de passer au tip suivant (random, in-place sans
  fermeture/réouverture) ou de fermer.
- Une checkbox **« Show again later »** sur chaque tip pour éviter de le
  marquer comme vu, donc le garder dans la rotation.
- État « vu » multi-device synced via `<data_dir>/seen-tips.json` (même
  pattern que `claude-settings-presets.json`, `message-snippets.json`,
  `workspaces.json`).
- Filtrage par contraintes : un tip n'est candidat que si **toutes** ses
  contraintes sont satisfaites par l'environnement du client (plateforme, OS,
  providers activés).
- Section **Tips** dans le panneau Settings : liste des tips
  **disponibles dans l'environnement courant** (contraintes satisfaites)
  + état (vu/non vu), cliquable pour les rouvrir. Pas de badge contrainte,
  pas de ligne pour les tips filtrés out.
- Switch global ON/OFF de l'affichage automatique (per-device, localStorage).
- Bouton « Reset all seen » dans la même section.

### 0.2 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas de pondération / priorité.** Tirage uniforme parmi les tips
  disponibles non vus.
- **Pas de cycle / réaffichage automatique** quand tous les tips ont été vus.
  L'utilisateur reset manuellement depuis les Settings.
- **Pas de localisation.** EN-only, conforme au reste du projet.
- **Pas de telemetry / compteur de vues.** On stocke un timestamp ISO de
  « dernière vue », c'est tout.
- **Pas de catégorisation / tag des tips.**
- **Pas de hot-reload du manifest tips en dev.** Restart backend nécessaire
  pour qu'un nouveau `.md` soit pris en compte.
- **Pas de `min_app_version` dans les contraintes.** Différé si besoin.
- **Pas de set initial de tips fourni.** Ce spec décrit l'infra ; le contenu
  est ajouté hors-spec.
- **Pas d'auto-discover d'images** par convention de nommage. La convention
  `<key>-N.<ext>` est de l'organisation des fichiers ; l'auteur référence les
  images explicitement dans le markdown.
- **Pas de validation backend des keys** envoyées dans `update_seen_tips`. Le
  backend persiste ce qu'il reçoit (cohérent avec les autres configs
  synced) ; les keys inconnues sont ignorées à la lecture côté frontend.

### 0.3 Vocabulaire

| Terme | Définition |
|-------|------------|
| **Tip** | Une astuce contextuelle. Un fichier markdown `<key>.md` dans `frontend/public/tips/`. |
| **Key** | Identifiant unique d'un tip = nom du fichier sans `.md`. Format `[a-z0-9-]+`. Sert de clé dans `seen-tips.json` et le manifest. |
| **Manifest** | Dict en mémoire côté backend, construit au boot par scan du dossier tips et parsing du front-matter. Push au client via WS (bootstrap + à chaque connexion). Ne contient pas le corps markdown. |
| **Seen** | Un tip est « vu » si sa key apparaît dans `seen-tips.json`. La valeur associée est le timestamp ISO de la dernière mise à jour. |
| **Disponible** | Un tip est « disponible » pour un client donné si **toutes** ses contraintes sont satisfaites par l'environnement courant. |
| **Candidate** | Un tip disponible **ET** non-vu. Le pool dans lequel on tire le random. |
| **Show again later** | La checkbox affichée sur chaque tip qui, si cochée à la fermeture / au next-tip, retire le tip de `seen-tips.json` (ou empêche son ajout). |

---

## 1. Fichiers de tips

### 1.1 Emplacement

Tous les tips vivent **à plat** dans `frontend/public/tips/`, à côté des
images associées :

```
frontend/public/tips/
├── alt-shift-m-message-input.md
├── alt-shift-m-message-input-1.webp
├── alt-shift-m-message-input-2.webp
├── drag-files-to-attach.md
├── drag-files-to-attach-1.webp
└── …
```

Convention de nommage :

- **Fichier markdown** : `<key>.md` où `<key>` est en kebab-case (lettres
  minuscules, chiffres, tirets ; format `[a-z0-9-]+`).
- **Images associées** (optionnel) : `<key>-N.<ext>` avec `N` un nombre
  incrémental à partir de 1, `<ext>` ∈ `{webp, png, jpg, svg, gif}`. Cette
  convention sert à l'organisation ; l'auteur référence explicitement chaque
  image dans le markdown.
- **Pas de sous-dossier.** Tout est à plat pour simplifier le scan et la
  résolution des URLs.

### 1.2 Front-matter

Chaque `.md` commence par un front-matter YAML standard (délimité par deux
lignes `---`) :

```markdown
---
title: "Use Alt+Shift+M to focus the message input"
platform: [desktop]
os: [mac, linux, windows]
providers_any: [claude_code]
---

Press **Alt+Shift+M** anywhere in the app to jump straight to the
message input. Works whether you're in a session or browsing projects.

![Demo](/static/tips/alt-shift-m-message-input-1.webp)
```

Schéma des clés autorisées :

| Clé | Type | Obligatoire | Valeurs autorisées | Sémantique si absent |
|-----|------|-------------|---------------------|----------------------|
| `title` | string | **oui** | n'importe quel string non-vide après `trim()` | — (tip rejeté du manifest) |
| `platform` | array de string | non | `mobile`, `desktop` | applicable aux deux |
| `os` | array de string | non | `mac`, `linux`, `windows` | applicable à tous les OS connus |
| `providers_any` | array de string | non | n'importe quelle clé de provider (`claude_code`, `codex`, …) | aucune contrainte provider |
| `providers_all` | array de string | non | idem `providers_any` | aucune contrainte provider |

**Convention de représentation, à respecter strictement** :

- **Absence d'une clé = pas de contrainte sur cette dimension** (équivaut à
  « toutes les valeurs »). C'est le cas par défaut, on ne met pas la clé.
- **Présence d'une clé = TOUJOURS un array**, même pour une seule valeur :
  `platform: [desktop]` et **non** `platform: desktop`. Cette uniformité
  simplifie le parsing et évite les ambiguïtés de coercion YAML.
- Un array vide (`platform: []`) est traité comme « aucune valeur ne
  satisfait » → le tip ne s'affichera jamais. Légal mais déconseillé ; sert
  à désactiver temporairement un tip.

### 1.3 Corps markdown

- Rendu via la pipeline existante `renderMarkdown` (`frontend/src/utils/markdown.js`) :
  markdown-it-async + shiki + DOMPurify.
- Pas d'option spéciale, pas de plugin custom, pas de resolver d'images.
- Les images sont référencées par **URL absolue commençant par
  `/static/tips/...`**. Vite expose `frontend/public/tips/` à
  `/static/tips/` en dev (parce que `base: '/static'`), et BlackNoise expose
  `src/twicc/static/frontend/tips/` au même chemin en prod.
- HTML brut interdit (`html: false`), comme partout ailleurs dans l'app.
- DOMPurify est appliqué — pas de SVG inline arbitraire.

### 1.4 Validation au scan

Au scan du dossier (au boot backend), pour chaque `<key>.md` :

1. La key (nom de fichier sans `.md`) doit matcher `[a-z0-9-]+`. Sinon →
   tip rejeté, warning loggé.
2. Le fichier doit commencer par un front-matter YAML valide (entre deux
   lignes `---`). Sinon → tip rejeté.
3. `title` doit être présent, string, non-vide après `trim()`. Sinon → tip
   rejeté.
4. Si `platform` / `os` / `providers_any` / `providers_all` sont présents :
   doivent être des arrays de strings. Sinon → tip rejeté.
5. Les valeurs des arrays doivent appartenir aux valeurs autorisées pour la
   clé (`mobile`/`desktop` pour `platform`, `mac`/`linux`/`windows` pour
   `os`). Sinon → tip rejeté.
6. Pour `providers_any` / `providers_all`, on accepte des valeurs inconnues
   du backend (extensibilité future), mais on loggue un info `Tip <key>:
   unknown provider '<x>' in providers_any` pour signaler.

En cas d'erreur, le tip est **exclu** du manifest (donc invisible côté
frontend) et une ligne warning est loggée via le module `tips_manifest`.
Pas de crash, pas d'erreur 500. Les autres tips continuent de fonctionner.

---

## 2. État synced — `seen-tips.json`

### 2.1 Path et format

Path : `<data_dir>/seen-tips.json` (résolu par un nouveau
`get_seen_tips_path()` dans `src/twicc/paths.py`).

Format :

```json
{
  "alt-shift-m-message-input": "2026-05-15T14:23:11.000Z",
  "drag-files-to-attach": "2026-05-16T09:11:42.000Z"
}
```

- Dict simple `{<tip_key>: <ISO timestamp UTC>}`.
- La valeur est le timestamp ISO 8601 UTC de la dernière mise à jour côté
  frontend. Sert à afficher « Seen 3 days ago » dans la liste Settings.
- Une key absente = tip **non-vu**.
- Une key présente = tip **vu**, indépendamment de la valeur exacte.
- Fichier autoritaire : pas de DB miroir, pas de cache séparé. Le backend
  lit/écrit ce fichier à chaque opération.

### 2.2 Mutations possibles

Quatre opérations côté frontend, qui aboutissent toutes à un **envoi du
state complet** au backend (cohérent avec le reste : presets, snippets,
workspaces) :

| Opération | Effet sur l'objet local | Source |
|-----------|------------------------|--------|
| `markSeen(key)` | `state[key] = now()` (refresh timestamp) | Close / Escape / Next-tip **avec checkbox décochée** (via `commitState`) |
| `unmarkSeen(key)` | `delete state[key]` (idempotent) | Close / Escape / Next-tip **avec checkbox cochée** (via `commitState`) |
| `resetAllSeen()` | `state = {}` | Bouton Settings |
| `applySeenTips(remote)` | `state = remote` | Push WS reçu |

Après chaque mutation locale (`markSeen` / `unmarkSeen` / `resetAllSeen`),
le store envoie immédiatement le nouvel état complet via WS
`update_seen_tips`.

### 2.3 Cas particuliers

- **Stale entries** (tip supprimé / renommé) : la key reste dans le fichier
  mais n'apparaît pas dans le manifest. Elle est ignorée au filtrage. **Pas
  de prune automatique en v1** (taille négligeable : < 1 KB pour 100 tips).
- **Tip renommé** : nouvelle key = non vu. Si le contenu est identique,
  l'utilisateur le verra à nouveau une fois. Acceptable.
- **Reset all** : `state = {}`, broadcast à tous les clients.

---

## 3. Évaluation des contraintes

### 3.1 Environnement client

Construit côté frontend, en `computed` reactif :

```js
const env = computed(() => ({
  platform: settings._isTouchDevice ? 'mobile' : 'desktop',
  os: settings.os,                              // 'mac' | 'linux' | 'windows' | null
  enabledProviders: settings.enabledProviders,  // ['claude_code', 'codex', …]
}));
```

Le `computed` se met à jour automatiquement si l'un des inputs change (par
exemple toggle d'un provider à chaud). Les ticks du scheduler relisent
`env` à chaque tentative d'affichage.

### 3.2 Fonction de filtre

Pure function, partagée entre le scheduler et la section Settings :

```js
export function isTipAvailable(tip, env) {
  if (tip.platform && !tip.platform.includes(env.platform)) {
    return false;
  }
  if (tip.os) {
    // Si l'OS est inconnu (env.os === null) ET le tip a une contrainte OS,
    // on filtre out par sécurité (on ne devine pas un OS exotique).
    if (env.os === null) return false;
    if (!tip.os.includes(env.os)) return false;
  }
  if (tip.providers_any && tip.providers_any.length > 0) {
    const anyEnabled = tip.providers_any.some((p) => env.enabledProviders.includes(p));
    if (!anyEnabled) return false;
  }
  if (tip.providers_all && tip.providers_all.length > 0) {
    const allEnabled = tip.providers_all.every((p) => env.enabledProviders.includes(p));
    if (!allEnabled) return false;
  }
  return true;
}
```

`providers_any` et `providers_all` peuvent être combinés (les deux doivent
passer). En pratique on n'utilisera presque que `providers_any`.

### 3.3 Détection OS — extension de `settings.js`

Aujourd'hui `settings.js` expose seulement `isMac` (via
`navigator.platform.startsWith('Mac') || /Macintosh/.test(userAgent)`). À
étendre :

```js
// state init (one-time, at store creation)
const ua = navigator.userAgent || '';
const plat = navigator.platform || '';

state._isMac     = plat.startsWith('Mac') || /Macintosh/.test(ua);
state._isLinux   = /Linux/i.test(plat) && !/Android/i.test(ua);
state._isWindows = /Win/i.test(plat);

// getters
isMac:     (s) => s._isMac,
isLinux:   (s) => s._isLinux,
isWindows: (s) => s._isWindows,
os: (s) => s._isMac ? 'mac' : s._isLinux ? 'linux' : s._isWindows ? 'windows' : null,
```

(`navigator.platform` est déprécié mais reste plus stable que
`userAgentData` pour la compatibilité Safari < 16 et Firefox.)

---

## 4. Architecture

```
┌─────────────────────────────────────────────────┐         ┌─────────────────────────────────────┐
│ Frontend                                        │         │ Backend                             │
│                                                 │         │                                     │
│  App.vue                                        │         │  paths.py                           │
│   ├─ <Notivue/>                                 │         │   ├─ get_seen_tips_path()           │
│   └─ useTipScheduler()  ────────────────────┐   │         │   └─ get_tips_assets_dir()          │
│                                             │   │         │                                     │
│  composables/useTipScheduler.js             │   │         │  tips_manifest.py        (new)      │
│   ├─ polls every 1 min                      │   │         │   ├─ init_manifest()  (boot)        │
│   ├─ gate: now >= nextEligibleTime          │   │         │   ├─ parse_front_matter()           │
│   └─ guards → showTipToast(key)             │   │         │   └─ get_manifest() → in-memory     │
│                                             ▼   │         │                                     │
│  components/tips/TipToast.vue ◄─ Notivue toast  │         │  seen_tips.py            (new)      │
│   ├─ markdown rendering (renderMarkdown)        │         │   ├─ read_seen_tips()               │
│   ├─ "Show again later" checkbox                │         │   └─ write_seen_tips() (atomic)     │
│   └─ "Next tip" button                          │         │                                     │
│                                                 │         │                                     │
│  stores/tips.js                                 │         │                                     │
│   ├─ manifest, seenTips, enabled, current…      │  ◄─WS─► │  asgi.py                            │
│   ├─ applyManifest / applySeenTips              │         │   ├─ on connect: send manifest +    │
│   ├─ markSeen / unmarkSeen / resetAllSeen       │         │   │   seen_tips                     │
│   └─ pickRandom / getCandidates                 │         │   └─ handle update_seen_tips        │
│                                                 │         │      (write + broadcast)            │
│  composables/useWebSocket.js                    │         │                                     │
│   ├─ on 'tips_manifest_pushed' → applyManifest  │         │  views.py                           │
│   ├─ on 'seen_tips_updated'    → applySeenTips  │         │   └─ /api/bootstrap/                │
│   └─ sendUpdateSeenTips(state)                  │         │      includes manifest + seen_tips  │
│                                                 │         │                                     │
│  components/app/SettingsPopover.vue             │         │                                     │
│   └─ TipsSettings.vue (new section)             │         │                                     │
│       ├─ enable toggle (localStorage)           │         │                                     │
│       ├─ "Reset all seen" button                │         │                                     │
│       └─ tips list (click → showTipToast)       │         │                                     │
└─────────────────────────────────────────────────┘         └─────────────────────────────────────┘
```

Data flow :

1. **Boot backend** : `init_manifest()` parse tous les fichiers de
   `get_tips_assets_dir()`, construit le manifest in-memory.
2. **Bootstrap HTTP** (`/api/bootstrap/`) : inclut `tips_manifest` et
   `seen_tips`. Frontend hydrate les stores avant le mount Vue.
3. **WS connect** (`UpdatesConsumer`) : push `tips_manifest_pushed` +
   `seen_tips_updated` (cas reconnect ou changement entre bootstrap et WS
   open).
4. **Mutation côté client** : `markSeen` / `unmarkSeen` / `resetAllSeen` →
   store met à jour son état local immédiatement → envoie le nouvel état
   complet via WS `update_seen_tips` → backend écrit le fichier
   atomiquement → broadcast `seen_tips_updated` à tous les clients (sender
   inclus, pour confirmation).
5. **Pas de versioning multi-write** : last-write-wins. Acceptable vu la
   rareté des conflits (un seul utilisateur, mutations rares).

---

## 5. Backend

### 5.1 `src/twicc/paths.py`

Ajouter deux helpers :

```python
def get_seen_tips_path() -> Path:
    return get_data_dir() / "seen-tips.json"


def get_tips_assets_dir() -> Path:
    """Directory holding tip .md and image files.

    Resolved differently in dev vs install:
    - DEV_MODE: read directly from `frontend/public/tips/` so that Vite's hot reload
      is the source of truth.
    - Installed: read from `FRONTEND_DIST_DIR / "tips"`, populated by hatch_build.py
      (which runs `npm run build` and copies `frontend/public/` into the wheel).
    """
    from django.conf import settings as django_settings
    if django_settings.DEV_MODE:
        return django_settings.PACKAGE_DIR.parent.parent / "frontend" / "public" / "tips"
    return django_settings.FRONTEND_DIST_DIR / "tips"
```

Le helper `get_tips_assets_dir` est nécessaire parce qu'en dev, Vite sert
directement `frontend/public/tips/` (le backend ne fait pas tourner `npm
run build`). En prod, les fichiers sont copiés dans le wheel via
`hatch_build.py` (qui inclut déjà `static/frontend/**`).

### 5.2 `src/twicc/tips_manifest.py` (nouveau)

Module qui scanne le dossier et expose un manifest en mémoire.

```python
import logging
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[a-z0-9-]+$")
PLATFORM_VALUES = {"mobile", "desktop"}
OS_VALUES = {"mac", "linux", "windows"}


class TipMeta(NamedTuple):
    key: str
    title: str
    platform: list[str] | None
    os: list[str] | None
    providers_any: list[str] | None
    providers_all: list[str] | None


_manifest: dict[str, TipMeta] = {}


def init_manifest() -> None:
    """Called once at startup. Scans the tips dir and fills the in-memory manifest."""
    global _manifest
    from twicc.paths import get_tips_assets_dir
    _manifest = scan_tips_dir(get_tips_assets_dir())
    logger.info("Tips manifest: %d tips loaded", len(_manifest))


def get_manifest() -> dict[str, TipMeta]:
    return _manifest


def manifest_to_dict() -> dict[str, dict]:
    """JSON-serializable form for the wire protocol."""
    return {
        key: {
            "title": tip.title,
            "platform": tip.platform,
            "os": tip.os,
            "providers_any": tip.providers_any,
            "providers_all": tip.providers_all,
        }
        for key, tip in _manifest.items()
    }


def scan_tips_dir(directory: Path) -> dict[str, TipMeta]:
    """Pure function: scan + parse + validate. Returns valid tips only.

    Invalid tips are logged (warning) and excluded. Never raises.
    """
    result: dict[str, TipMeta] = {}
    if not directory.is_dir():
        logger.warning("Tips directory not found: %s", directory)
        return result

    for path in sorted(directory.glob("*.md")):
        key = path.stem
        if not KEY_PATTERN.match(key):
            logger.warning("Tip %s: key does not match [a-z0-9-]+, skipped", key)
            continue
        try:
            meta = _parse_tip_file(key, path)
        except ValueError as exc:
            logger.warning("Tip %s: %s, skipped", key, exc)
            continue
        result[key] = meta
    return result


def _parse_tip_file(key: str, path: Path) -> TipMeta:
    text = path.read_text(encoding="utf-8")
    fm, _body = _split_front_matter(text)
    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing or invalid 'title'")
    return TipMeta(
        key=key,
        title=title.strip(),
        platform=_validate_array(key, fm, "platform", PLATFORM_VALUES),
        os=_validate_array(key, fm, "os", OS_VALUES),
        providers_any=_validate_array(key, fm, "providers_any", allowed=None),
        providers_all=_validate_array(key, fm, "providers_all", allowed=None),
    )


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Returns (yaml_dict, body) or raises ValueError."""
    # Implementation: detect `^---\n…\n---\n` and parse with pyyaml.
    ...


def _validate_array(
    key: str, fm: dict, field: str, allowed: set[str] | None
) -> list[str] | None:
    if field not in fm:
        return None
    value = fm[field]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"'{field}' must be an array of strings")
    if allowed is not None:
        bad = [x for x in value if x not in allowed]
        if bad:
            raise ValueError(f"'{field}' has invalid values: {bad}")
    else:
        # providers_any/providers_all: warn on unknown providers but accept
        # (forward compatibility)
        # Comparison against the live provider registry is intentionally avoided
        # here — the manifest is built once at boot and providers may be enabled
        # later. The frontend's constraint evaluator does the real check.
        pass
    return value
```

Notes :

- **Dépendance YAML** : on ajoute `pyyaml` via `uv add pyyaml`. Une alternative
  (mini-parser maison) serait moins robuste pour peu de gain — pyyaml est ~150
  KB, déjà transitivement chargé par d'autres outils, et c'est le standard.
- `init_manifest()` est appelé une fois au boot, dans le module Django
  approprié (voir 5.5).
- Le scan ne lit **pas** le corps markdown — le frontend va le chercher en
  HTTP (`/static/tips/<key>.md`).
- **Re-scan à chaud : non en v1.** Ajouter un tip nécessite restart backend.

### 5.3 `src/twicc/seen_tips.py` (nouveau)

Module pour read/write atomique. Calqué sur `claude_settings_presets.py`.

```python
import logging
import os
import tempfile
from pathlib import Path

import orjson

from twicc.paths import get_seen_tips_path

logger = logging.getLogger(__name__)


def read_seen_tips() -> dict[str, str]:
    """Returns the persisted state, or {} if file missing or invalid."""
    path = get_seen_tips_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except orjson.JSONDecodeError:
        logger.warning("seen-tips.json is invalid JSON, returning empty state")
        return {}
    if not isinstance(data, dict):
        logger.warning("seen-tips.json is not a dict, returning empty state")
        return {}
    # Defensive: drop non-string keys/values silently
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def write_seen_tips(state: dict[str, str]) -> None:
    """Atomic write via tempfile + os.replace."""
    path = get_seen_tips_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = orjson.dumps(state, option=orjson.OPT_INDENT_2)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, delete=False, prefix=".seen-tips-", suffix=".tmp"
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
```

### 5.4 `src/twicc/views.py` — bootstrap

Dans la vue `bootstrap()`, ajouter deux clés :

```python
from twicc.seen_tips import read_seen_tips
from twicc.tips_manifest import manifest_to_dict

return JsonResponse({
    # … existing keys …
    "tips_manifest": manifest_to_dict(),
    "seen_tips": read_seen_tips(),
})
```

### 5.5 Initialisation au boot

`init_manifest()` doit être appelée une fois après que Django est chargé,
de préférence après l'init des settings et avant que l'app accepte des
requêtes.

Emplacement préféré : `src/twicc/apps.py` dans `ready()` de l'`AppConfig`,
ou si plus pratique au lifespan ASGI (voir `asgi.py`). À trancher à
l'implem ; cohérent avec ce qui est fait pour les autres init similaires
(price sync, etc.).

### 5.6 `src/twicc/asgi.py` — WS handlers

Modifications dans `UpdatesConsumer`.

**À la connexion** (dans le bloc qui envoie déjà
`agent_settings_presets_updated`, `terminal_config_updated`, etc.) :

```python
# Tips manifest (read-only, in-memory snapshot)
await self.send_json({
    "type": "tips_manifest_pushed",
    "manifest": manifest_to_dict(),
})

# Seen tips state
await self.send_json({
    "type": "seen_tips_updated",
    "seen_tips": await sync_to_async(read_seen_tips)(),
})
```

**Nouveau handler entrant** pour `update_seen_tips` :

```python
async def receive_update_seen_tips(self, msg: dict) -> None:
    state = msg.get("seen_tips", {})
    if not isinstance(state, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in state.items()
    ):
        logger.warning("Invalid update_seen_tips payload, ignoring")
        return
    await sync_to_async(write_seen_tips)(state)
    await self.channel_layer.group_send("updates", {
        "type": "group.broadcast",
        "payload": {"type": "seen_tips_updated", "seen_tips": state},
    })
```

Pas de version / clock : last-write-wins, comme les presets.

---

## 6. Frontend

### 6.1 Store `frontend/src/stores/tips.js` (nouveau)

```js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { isTipAvailable } from './tipsConstraints';

const LS_ENABLED_KEY = 'twicc.tips.enabled';

export const useTipsStore = defineStore('tips', () => {
  // Manifest : { <key>: { title, platform, os, providers_any, providers_all } }
  const manifest = ref({});

  // Seen state : { <key>: ISO timestamp }
  const seenTips = ref({});

  // Currently displayed toast tip key (in-memory, not synced).
  // Watched by TipToast.vue to swap content in-place.
  const currentToastTipKey = ref(null);

  // Epoch ms at which the scheduler is allowed to show a new tip again.
  // Set at boot to (now + FIRST_TIP_DELAY_MS), then on every dismiss to
  // (now + TIP_COOLDOWN_MS). In-memory, per-tab.
  const nextEligibleTime = ref(0);

  // Per-device on/off (localStorage). Default = ON.
  const _enabledLS = localStorage.getItem(LS_ENABLED_KEY);
  const enabled = ref(_enabledLS === null ? true : _enabledLS === 'true');

  function applyManifest(remote) {
    manifest.value = remote || {};
  }

  function applySeenTips(remote) {
    seenTips.value = remote || {};
  }

  function setEnabled(value) {
    enabled.value = !!value;
    localStorage.setItem(LS_ENABLED_KEY, String(enabled.value));
  }

  function _sendSeenTips() {
    // lazy import to avoid circular store ↔ composable
    import('@/composables/useWebSocket').then(({ useWebSocket }) => {
      useWebSocket().sendUpdateSeenTips(seenTips.value);
    });
  }

  function markSeen(key) {
    if (!manifest.value[key]) return;             // unknown key, ignore
    // Refresh the timestamp on every call : a user re-opening an already-seen
    // tip with the checkbox unchecked legitimately updates the "Seen X ago"
    // ordering. One small WS chatter per voluntary dismiss is acceptable.
    seenTips.value = { ...seenTips.value, [key]: new Date().toISOString() };
    _sendSeenTips();
  }

  function unmarkSeen(key) {
    if (!(key in seenTips.value)) return;
    const next = { ...seenTips.value };
    delete next[key];
    seenTips.value = next;
    _sendSeenTips();
  }

  function resetAllSeen() {
    if (Object.keys(seenTips.value).length === 0) return;
    seenTips.value = {};
    _sendSeenTips();
  }

  function getAvailableTips(env) {
    return Object.entries(manifest.value)
      .filter(([_, tip]) => isTipAvailable(tip, env))
      .map(([key, tip]) => ({ key, ...tip }));
  }

  function getCandidates(env) {
    return getAvailableTips(env).filter((t) => !(t.key in seenTips.value));
  }

  function pickRandom(candidates, exclude = []) {
    const pool = candidates.filter((t) => !exclude.includes(t.key));
    if (pool.length === 0) return null;
    return pool[Math.floor(Math.random() * pool.length)];
  }

  return {
    manifest, seenTips, currentToastTipKey, nextEligibleTime, enabled,
    applyManifest, applySeenTips, setEnabled,
    markSeen, unmarkSeen, resetAllSeen,
    getAvailableTips, getCandidates, pickRandom,
  };
});
```

### 6.2 Helper `frontend/src/stores/tipsConstraints.js` (nouveau)

Fonction pure, isolée du store (importable depuis le composable scheduler
et la section Settings sans charger le store entier) :

```js
export function isTipAvailable(tip, env) {
  if (tip.platform && !tip.platform.includes(env.platform)) return false;
  if (tip.os) {
    if (env.os === null) return false;            // unknown OS + constraint = filter out
    if (!tip.os.includes(env.os)) return false;
  }
  if (tip.providers_any && tip.providers_any.length > 0) {
    const any = tip.providers_any.some((p) => env.enabledProviders.includes(p));
    if (!any) return false;
  }
  if (tip.providers_all && tip.providers_all.length > 0) {
    const all = tip.providers_all.every((p) => env.enabledProviders.includes(p));
    if (!all) return false;
  }
  return true;
}
```

Note : pas de fonction d'explication d'indisponibilité — la section
Settings n'affiche que les tips disponibles, donc on n'a jamais à
expliquer pourquoi un tip n'est pas affiché.

### 6.3 Composable `frontend/src/composables/useTipScheduler.js` (nouveau)

Le scheduler **n'utilise pas un timer absolu fixe**. Il polle régulièrement
mais ne propose un tip que si suffisamment de temps s'est écoulé depuis la
**dernière fermeture** d'un tip — c'est `tipsStore.nextEligibleTime` qui
porte cette information. Ainsi, un tip resté affiché 25 min ne déclenche
pas l'apparition d'un nouveau tip immédiatement après sa fermeture.

```js
import { onMounted, onBeforeUnmount } from 'vue';
import { useTipsStore } from '@/stores/tips';
import { useSettingsStore } from '@/stores/settings';
import { hasBlockingOverlay } from '@/utils/focusGuard';
import { showTipToast } from '@/components/tips/showTipToast';

// --- Constants (easy to tune) -----------------------------------------------
// Cooldown between consecutive tip displays, measured from the moment the
// user voluntarily dismisses the previous tip (Close, Escape, or "Next tip"
// returning no further candidate). Default 2h — bump as needed.
export const TIP_COOLDOWN_MS    = 2 * 60 * 60_000;   // 2 hours

// Delay between app mount and the first tip attempt.
export const FIRST_TIP_DELAY_MS = 60_000;            // 60 seconds

// How often the scheduler wakes up to check whether it can show a tip.
// Short enough to feel responsive once the cooldown is over, long enough
// to be invisible. NOT the inter-tip delay.
export const SCHEDULER_POLL_MS  = 60_000;            // 1 minute
// ---------------------------------------------------------------------------

export function useTipScheduler() {
  const tipsStore = useTipsStore();
  const settings = useSettingsStore();

  let pollHandle = null;

  function tryShowTip() {
    if (!tipsStore.enabled) return;
    if (tipsStore.currentToastTipKey !== null) return;     // a tip is already up
    if (Date.now() < tipsStore.nextEligibleTime) return;   // cooldown not over
    if (hasBlockingOverlay()) return;
    if (document.visibilityState !== 'visible') return;

    const env = {
      platform: settings._isTouchDevice ? 'mobile' : 'desktop',
      os: settings.os,
      enabledProviders: settings.enabledProviders,
    };

    const candidates = tipsStore.getCandidates(env);
    if (candidates.length === 0) return;

    const tip = tipsStore.pickRandom(candidates);
    if (tip) showTipToast(tip.key);
    // nextEligibleTime is NOT updated here. It gets bumped on dismiss (see
    // TipToast.vue), so a tip that stays open for hours doesn't trigger an
    // immediate follow-up the instant it closes.
  }

  onMounted(() => {
    // Initial cooldown : nothing can show until FIRST_TIP_DELAY_MS has elapsed.
    tipsStore.nextEligibleTime = Date.now() + FIRST_TIP_DELAY_MS;
    pollHandle = setInterval(tryShowTip, SCHEDULER_POLL_MS);
  });

  onBeforeUnmount(() => {
    if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
  });
}
```

Instancié une fois dans `App.vue` (`<script setup>` → `useTipScheduler()`).

**Ré-affirmation de la sémantique du cooldown** :

- Premier tip dispo `FIRST_TIP_DELAY_MS` après mount.
- À chaque **fermeture volontaire** d'un tip (Close, ESC, ou Next-tip qui
  retombe sur 0 candidat → fermeture), `nextEligibleTime` est repoussé de
  `TIP_COOLDOWN_MS` à compter de **maintenant**.
- Tant qu'un tip est affiché, le scheduler ne fait rien (early-return sur
  `currentToastTipKey !== null`). Donc même si `nextEligibleTime` est dans
  le passé, on n'enchaîne pas.
- Click sur « Next tip » qui amène un nouveau tip : pas de changement de
  `nextEligibleTime` (l'utilisateur est toujours en train de consommer la
  chaîne ; le cooldown sera armé quand il fermera vraiment).

### 6.4 Component `frontend/src/components/tips/TipToast.vue` (nouveau)

Composant Vue rendu par Notivue via `toast.custom(TipToast, …)`.

```vue
<template>
  <div class="tip-toast" @keydown.esc="onClose">
    <header class="tip-header">
      <wa-icon name="lightbulb" />
      <span class="tip-title">{{ tip?.title }}</span>
      <button class="tip-close" @click="onClose" aria-label="Close tip">
        <wa-icon name="xmark" />
      </button>
    </header>

    <div v-if="loading" class="tip-loading">Loading tip…</div>
    <div v-else-if="error" class="tip-error">Failed to load tip content.</div>
    <div v-else class="tip-body" v-html="bodyHtml" />

    <footer class="tip-footer">
      <label class="tip-show-again">
        <input type="checkbox" v-model="showAgainLater" />
        <span>Show again later</span>
      </label>
      <wa-button
        v-if="hasMoreCandidates"
        size="small"
        @click="onNextTip"
      >
        Next tip
        <wa-icon slot="end" name="chevron-right" />
      </wa-button>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useTipsStore } from '@/stores/tips';
import { useSettingsStore } from '@/stores/settings';
import { renderMarkdown } from '@/utils/markdown';
import { stripFrontMatter } from '@/utils/frontMatter';
import { TIP_COOLDOWN_MS } from '@/composables/useTipScheduler';

const props = defineProps({
  notivueItem: { type: Object, required: true },   // notivue auto-injects
});

const tipsStore = useTipsStore();
const settings = useSettingsStore();

const tip = computed(() => {
  const k = tipsStore.currentToastTipKey;
  if (!k) return null;
  return { key: k, ...tipsStore.manifest[k] };
});

const bodyHtml = ref('');
const loading = ref(false);
const error = ref(false);
const showAgainLater = ref(false);
const bodyCache = new Map();   // key → rendered html

const env = computed(() => ({
  platform: settings._isTouchDevice ? 'mobile' : 'desktop',
  os: settings.os,
  enabledProviders: settings.enabledProviders,
}));

const hasMoreCandidates = computed(() => {
  if (!tip.value) return false;
  const candidates = tipsStore.getCandidates(env.value);
  return candidates.filter((c) => c.key !== tip.value.key).length > 0;
});

async function loadBody(key) {
  if (bodyCache.has(key)) {
    bodyHtml.value = bodyCache.get(key);
    loading.value = false;
    error.value = false;
    return;
  }
  loading.value = true;
  error.value = false;
  try {
    const raw = await fetch(`/static/tips/${key}.md`).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.text();
    });
    const body = stripFrontMatter(raw);
    const html = await renderMarkdown(body);
    bodyCache.set(key, html);
    bodyHtml.value = html;
  } catch (e) {
    console.error('Failed to load tip', key, e);
    error.value = true;
  } finally {
    loading.value = false;
  }
}

// Commit helper : called only on voluntary close / next-tip, never on display.
// Checkbox unchecked = "I've seen it" → markSeen.
// Checkbox checked  = "Show me again later" → unmarkSeen (idempotent if absent).
function commitState(key) {
  if (!key) return;
  if (showAgainLater.value) {
    tipsStore.unmarkSeen(key);
  } else {
    tipsStore.markSeen(key);
  }
}

// React to currentToastTipKey changes : only reset checkbox + load new body.
// No mark/unmark here. Mark only happens on voluntary dismiss / swap.
watch(() => tipsStore.currentToastTipKey, async (newKey) => {
  if (!newKey) return;
  showAgainLater.value = false;
  await loadBody(newKey);
}, { immediate: true });

// Internal helper : tear down the toast and arm the cooldown.
// Called by both onClose and the "no more candidates" branch of onNextTip,
// so we don't double-commit the same tip's seen state.
function teardown() {
  tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS;
  tipsStore.currentToastTipKey = null;
  props.notivueItem.clear();
}

function onClose() {
  commitState(tipsStore.currentToastTipKey);
  teardown();
}

function onNextTip() {
  const key = tipsStore.currentToastTipKey;
  commitState(key);
  showAgainLater.value = false;
  const candidates = tipsStore.getCandidates(env.value);
  const next = tipsStore.pickRandom(candidates, [key]);
  if (!next) {
    teardown();
    return;
  }
  tipsStore.currentToastTipKey = next.key;   // triggers watch above
}
</script>
```

Notes :

- `bodyCache` est une `Map` locale à l'instance du composant (donc à la
  durée de vie du toast Notivue). Utile principalement lors d'un swap
  « Next tip » → revenir en arrière (si on l'autorisait un jour) ou
  ré-afficher le même tip sans refetcher. Une fois le toast fermé,
  l'instance est démontée et le cache est GC. Si on voulait un cache
  inter-toasts, il faudrait le sortir au niveau module ; pas en v1.
- `stripFrontMatter` est un utilitaire pur (regex sur le préfixe `---\n…\n---\n`).
  Pas besoin de parser le YAML côté frontend — le manifest contient déjà
  toutes les méta-données.
- Le `watch` avec `immediate: true` couvre le premier rendu : à l'ouverture
  du toast, il déclenche `loadBody(newKey)` et reset la checkbox. Aucun
  marquage `seen` n'est fait ici — le seen-state n'évolue qu'à la
  fermeture / au swap volontaires (voir `commitState`).

### 6.5 Helper `frontend/src/components/tips/showTipToast.js` (nouveau)

```js
import { useToast } from '@/composables/useToast';
import { useTipsStore } from '@/stores/tips';
import TipToast from './TipToast.vue';

export function showTipToast(key) {
  const tipsStore = useTipsStore();
  if (!tipsStore.manifest[key]) return;
  // If a toast is already showing, just swap the key in-place.
  if (tipsStore.currentToastTipKey !== null) {
    tipsStore.currentToastTipKey = key;
    return;
  }
  tipsStore.currentToastTipKey = key;
  useToast().custom(TipToast, {
    duration: Infinity,
    hideOnHover: false,
    ariaLive: 'polite',
  });
}
```

(La signature exacte de `toast.custom` est à vérifier dans la pipeline
Notivue existante ; le contrat est : un composant Vue + des options dont
`duration: Infinity` pour sticky.)

### 6.6 Section Settings — `frontend/src/components/settings/TipsSettings.vue` (nouveau)

Sous-composant inclus dans `SettingsPopover.vue`. La liste n'affiche que
**les tips disponibles dans l'environnement courant** (contraintes
satisfaites). Pas de badge contrainte, pas de ligne « unavailable » : un
tip filtré par les contraintes est simplement absent de la liste. La
filtrage est implicite côté UI ; côté code, on réutilise
`tipsStore.getAvailableTips(env)`.

Layout :

```
┌─────────────────────────────────────────────────────┐
│ Tips                                                │
├─────────────────────────────────────────────────────┤
│ Display tips automatically       [●     ] ON         │
│                                                     │
│ [Reset all seen tips]                               │
│                                                     │
│ ─── All tips ────────────────────────               │
│                                                     │
│ ✓ Use Alt+Shift+M to focus the message input        │
│   Seen 3 days ago                                   │
│                                                     │
│ ○ Drag files into a session to attach them          │
│   Not yet seen                                      │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Comportement :

- **Toggle « Display tips automatically »** → `tipsStore.setEnabled(value)`.
  Affiche un texte explicatif sous le toggle quand il est OFF :
  *« Tips will only appear when you click them from this list. »*
- **Bouton « Reset all seen tips »** : ouvre une `wa-dialog` de confirmation
  (`« This will mark all tips as unseen. They may appear again on the next
  tick. »`) → `tipsStore.resetAllSeen()`. Disabled si `seenTips` est vide.
- **Liste de tips** : itère sur `tipsStore.getAvailableTips(env)`, ordre
  alphabétique de title. Chaque ligne :
  - **Icône d'état** : `✓` si seen, `○` si non-seen.
  - **Titre** : cliquable, ouvre le toast.
  - **Sous-ligne** : `Seen X ago` (helper de format relatif existant côté
    frontend, à identifier dans `frontend/src/utils/`) ou `Not yet seen`.
- **Click sur une ligne** : ferme le popover Settings (via l'API du
  `wa-popover` parent, à exposer par `SettingsPopover.vue` au besoin) puis
  `showTipToast(key)`. La fermeture systématique évite tout conflit visuel
  avec le toast.

Le toast déclenché depuis Settings suit **exactement le même cycle** que
le toast automatique (pas de marquage à l'affichage, commit-on-dismiss en
fonction de la checkbox, next-tip in-place). C'est le même composant, le
même store, le même helper.

**Cas vide** : si `getAvailableTips(env)` retourne `[]`, on affiche
*« No tips available yet »* à la place de la liste.

**Format relatif « X ago »** : **réutiliser le helper existant côté
frontend** (à localiser dans `frontend/src/utils/`). Ne pas créer de
nouvel utilitaire pour ça.

### 6.7 Intégration `SettingsPopover.vue`

Dans le computed `sections`, ajouter avant `notifications` :

```js
{ key: 'tips', label: 'Tips', icon: 'lightbulb' },
```

Et le bloc :

```vue
<section v-if="activeSection === 'tips'">
  <h3 class="settings-section-title">Tips</h3>
  <TipsSettings />
</section>
```

### 6.8 Composable `useWebSocket.js` (modification)

Ajouter handlers entrants dans le dispatcher de messages WS :

```js
case 'tips_manifest_pushed':
  useTipsStore().applyManifest(msg.manifest);
  break;
case 'seen_tips_updated':
  useTipsStore().applySeenTips(msg.seen_tips);
  break;
```

Et fonction sortante exportée :

```js
function sendUpdateSeenTips(state) {
  send({ type: 'update_seen_tips', seen_tips: state });
}
return { …, sendUpdateSeenTips };
```

### 6.9 Bootstrap hydration (`main.js`)

Dans la séquence existante qui applique les configs du bootstrap, ajouter :

```js
if (bootstrap.tips_manifest) {
  useTipsStore().applyManifest(bootstrap.tips_manifest);
}
if (bootstrap.seen_tips) {
  useTipsStore().applySeenTips(bootstrap.seen_tips);
}
```

### 6.10 Utilitaire `frontend/src/utils/frontMatter.js` (nouveau)

Pour `stripFrontMatter` utilisé par `TipToast.vue` :

```js
const FM_RE = /^---\r?\n[\s\S]*?\r?\n---\r?\n/;

export function stripFrontMatter(text) {
  return text.replace(FM_RE, '');
}
```

Pas besoin de parser le YAML ; on jette juste le bloc.

---

## 7. Scheduler — détails de comportement

### 7.1 Modèle temporel

Le scheduler n'utilise **pas** un intervalle absolu entre deux affichages.
Il utilise un **cooldown post-fermeture** : après chaque dismiss
volontaire d'un tip, on attend `TIP_COOLDOWN_MS` avant qu'un nouveau tip
puisse apparaître.

Conséquences :

- Si un tip reste ouvert pendant 25 min puis l'utilisateur le ferme, le
  prochain tip n'apparaîtra pas avant `TIP_COOLDOWN_MS` à compter de la
  fermeture (pas à compter du premier affichage).
- Si l'utilisateur enchaîne plusieurs tips via « Next tip », le cooldown
  ne démarre qu'à la fermeture finale (Close de la chaîne, ou Next-tip
  qui ne trouve plus de candidat → teardown).

Constantes (voir 6.3) :

| Nom | Défaut | Rôle |
|-----|--------|------|
| `FIRST_TIP_DELAY_MS` | 60 s | Délai avant que le premier tip puisse apparaître après mount |
| `TIP_COOLDOWN_MS` | 2 h | Cooldown entre la fermeture d'un tip et la possibilité du suivant |
| `SCHEDULER_POLL_MS` | 1 min | Fréquence du polling interne (ne dicte pas la fréquence d'affichage) |

`TIP_COOLDOWN_MS` est la variable à ajuster en priorité ; elle est exportée
nommément depuis `useTipScheduler.js` pour un grep facile.

### 7.2 Implémentation : polling avec gate

À chaque tick (`SCHEDULER_POLL_MS`), on vérifie une éligibilité temporelle
(`now >= tipsStore.nextEligibleTime`) **avant** de tester les autres gardes.
La valeur de `nextEligibleTime` est :

- **À mount d'`App.vue`** : `now + FIRST_TIP_DELAY_MS`.
- **À chaque teardown** (fermeture volontaire dans `TipToast.vue`) :
  `now + TIP_COOLDOWN_MS`.
- **Pas modifiée** lors d'un swap « Next tip » réussi.
- **Pas modifiée** lors d'un tick avorté par un garde (overlay, tab
  cachée, etc.).

### 7.3 Garde-fous (tous doivent passer dans cet ordre)

| # | Garde | Source | Pourquoi |
|---|-------|--------|----------|
| 1 | `tipsStore.enabled === true` | localStorage | switch global off |
| 2 | `tipsStore.currentToastTipKey === null` | store | un seul tip toast à la fois |
| 3 | `Date.now() >= tipsStore.nextEligibleTime` | store | cooldown post-dismiss pas écoulé |
| 4 | `hasBlockingOverlay() === false` | DOM query (`focusGuard.js`) | ne pas masquer une modale active |
| 5 | `document.visibilityState === 'visible'` | browser API | pas de tip si tab cachée |
| 6 | `candidates.length > 0` | store | rien à montrer = silence |

Si un garde échoue, le tick est sauté (pas de retry plus tôt, pas de log).
Le prochain tick (1 min plus tard) re-tente.

### 7.4 Cas « tous les tips vus »

- `candidates.length === 0` après filtrage par contraintes ET seen →
  silence permanent jusqu'à reset ou nouveau tip ajouté.
- L'utilisateur peut reset manuellement depuis Settings > Tips.

---

## 8. Toast UX — détails

### 8.1 Cycle de vie du seen-state

Le seen-state d'un tip ne change qu'à un **moment volontaire** : Close,
Escape, ou Next-tip. À l'ouverture du toast (random ou Settings), rien
n'est touché. La direction du changement est dictée par la checkbox
« Show again later ».

| Moment | Action |
|--------|--------|
| Toast affiché (mount, ou swap via Next-tip) | aucune action sur `seenTips`. La checkbox est reset à `false`. |
| Close / Escape, checkbox **décochée** | `markSeen(key)` (ajoute / refresh le timestamp) |
| Close / Escape, checkbox **cochée** | `unmarkSeen(key)` (retire la key — idempotent si pas présente) |
| Next-tip, checkbox décochée | `markSeen(currentKey)` puis swap |
| Next-tip, checkbox cochée | `unmarkSeen(currentKey)` puis swap |
| Next-tip sans candidat disponible | commit du courant (selon checkbox) puis teardown (= Close) |
| Browser tab fermé sans dismiss explicite | aucune action — pas de marquage involontaire |
| Reset all seen pendant qu'un toast est ouvert | `seenTips` effacé. À la fermeture, comportement habituel selon checkbox (idempotent si la key n'est déjà plus là). |

### 8.2 Swap « Next tip » (in-place)

Étapes lors du click « Next tip » :

1. **Commit du tip courant** via `commitState(currentKey)` :
   - checkbox cochée → `unmarkSeen(currentKey)` ;
   - checkbox décochée → `markSeen(currentKey)`.
2. Reset `showAgainLater` à `false`.
3. Pick `nextKey` = `pickRandom(getCandidates(env), [currentKey])`.
4. Si `nextKey === null` → `teardown()` (arme le cooldown +
   `currentToastTipKey = null` + clear Notivue). Plus aucun candidat.
5. Sinon : `tipsStore.currentToastTipKey = nextKey` → le watch s'occupe de
   `loadBody(nextKey)` et `showAgainLater = false` (idempotent vu l'étape
   2).

Pas de fermeture / réouverture Notivue : c'est le même toast qui change
de contenu. Le user perçoit une transition douce (skeleton pendant le
fetch).

### 8.3 Accessibilité

- Le toast est rendu par Notivue qui gère déjà `role="status"` et
  `aria-live="polite"`.
- Bouton Close : `aria-label="Close tip"`.
- Bouton Next tip : libellé textuel, pas besoin d'aria-label.
- Touche `Escape` ferme le toast (`@keydown.esc="onClose"` sur le root).
- Pas de focus trap (un toast n'est pas une modale).

### 8.4 Styling

- Toast plus large que les notifications standard (~480px max-width,
  responsive — sur mobile, prend toute la largeur moins padding).
- Header : icône lightbulb + titre tronqué + close.
- Body : markdown rendu, `max-height: 60vh` avec scroll vertical interne si
  dépasse.
- Footer : checkbox aligné à gauche, bouton « Next tip » aligné à droite,
  séparés par `space-between`.
- Theme : respecte `.wa-dark` comme tous les composants markdown (shiki
  dual-theme déjà géré par `renderMarkdown`).

---

## 9. Edge cases

| Cas | Comportement |
|-----|--------------|
| Front-matter mal formé | Tip exclu du manifest, warning log, autres tips OK |
| `title` manquant | Idem |
| Key avec caractères interdits | Idem |
| YAML invalide | Idem |
| `platform`/`os`/etc. présent mais pas un array | Idem (validation stricte au scan) |
| Stale entry dans `seen-tips.json` (tip supprimé) | Ignorée au filtrage (jamais matchée), reste persistée |
| Tip renommé | Nouvelle key = non vu, vue à nouveau une fois |
| Constraintes changées à chaud (provider activé/désactivé, ou bien rotation portrait↔landscape mais ça ne touche pas `_isTouchDevice`) | Pris en compte au tick suivant via reactivité Vue (l'`env` est computed) |
| Multi-tab simultané, mark concurrent | Last-write-wins, accepté |
| Multi-tab : `nextEligibleTime` est per-tab | Une autre tab peut afficher un tip pendant le cooldown de la première. Accepté ; affiner si pénible (synchroniser via localStorage event). |
| Browser offline / WS déconnecté au moment d'un mark-seen | L'état local est à jour ; `sendUpdateSeenTips` échoue silencieusement. Au reconnect, le push initial du backend re-synchronise (overwrite local). Le local peut diverger temporairement ; au reconnect, le remote gagne. Acceptable : si le seen est perdu, le tip réapparaîtra une fois, pas plus. |
| Browser tab fermé pendant qu'un tip est affiché | Aucun marquage (pas de mark eager). Le tip réapparaîtra une fois ouvert au prochain tour. Acceptable et conforme au modèle « seen = action volontaire ». |
| Plus aucun tip dispo après next-tip | `teardown()` (cooldown armé, toast clear) |
| Tip ouvert depuis Settings, on quitte les Settings | Toast reste affiché (lifecycle Notivue indépendant de l'état du popover) |
| Manifest vide (aucun .md dans le dossier) | `getCandidates` retourne `[]`, scheduler silencieux, Settings affiche `« No tips available yet »` |
| Réseau lent : fetch markdown long | Loader visible, pas de timeout dur (l'utilisateur peut close) |
| Fetch markdown échoue (404, 500) | Le composant affiche un message d'erreur dans le `tip-body`. Le toast reste interactif (close, next). |
| OS inconnu (`env.os === null`) ET `tip.os` défini | Tip filtré out (paranoia ; on ne devine pas l'OS) |
| Resize fenêtre passe de desktop à mobile (DevTools) | `_isTouchDevice` n'est pas observé en runtime (set au boot). Pas de re-évaluation des contraintes à chaud sur cette dimension. Acceptable : `_isTouchDevice` détecte du hardware, pas du viewport. |

---

## 10. Sécurité

- Le markdown est rendu via `renderMarkdown` qui sanitize avec DOMPurify.
  Les tips étant fournis dans le code source (commités), le risque XSS via
  tip malicieux se limite aux contributeurs du projet. La sanitisation
  reste un filet de sécurité cohérent avec le reste de l'app.
- Les images référencées dans le markdown sont laissées par DOMPurify et
  chargées par le browser ; aucune whitelist explicite de domaine, mais le
  bon usage est de pointer toujours vers `/static/tips/...`. Pas de
  validation hard.
- `seen-tips.json` est local au data dir : pas de surface réseau pour
  exfiltration.
- Pas de validation côté backend des keys envoyées dans `update_seen_tips`
  (un client pourrait envoyer une key inexistante). On l'écrira dans le
  fichier, mais elle sera ignorée à la lecture côté frontend.
- L'endpoint `/api/bootstrap/` est déjà derrière l'auth de l'app ;
  l'ajout des deux clés ne change pas la surface d'attaque.

---

## 11. Plan d'implémentation (haut niveau)

Découpé en étapes commitables indépendamment :

1. **Backend infra** :
   - `paths.py` : `get_seen_tips_path()`, `get_tips_assets_dir()`.
   - `tips_manifest.py` (scan + parse + manifest in-memory).
   - `seen_tips.py` (read/write atomique).
   - Init du manifest au boot (`apps.py` `ready()` ou ASGI lifespan).
   - `pyproject.toml` / `uv add pyyaml`.

2. **Backend wire** :
   - `views.py` : include dans `/api/bootstrap/`.
   - `asgi.py` : push on connect + handler `update_seen_tips`.

3. **Frontend infra** :
   - `stores/tips.js` + `stores/tipsConstraints.js`.
   - `utils/frontMatter.js`.
   - `composables/useWebSocket.js` (handlers + `sendUpdateSeenTips`).
   - `main.js` (hydration depuis bootstrap).
   - `settings.js` (détection OS : `_isLinux`, `_isWindows`, `os` getter).

4. **Frontend UI** :
   - `components/tips/TipToast.vue`.
   - `components/tips/showTipToast.js`.
   - `composables/useTipScheduler.js`.
   - `App.vue` : instancier `useTipScheduler()`.
   - `components/settings/TipsSettings.vue`.
   - `SettingsPopover.vue` : intégration section.

5. **Assets** (hors-spec mais nécessaires pour valider) :
   - 2-3 tips initiaux pour vérifier la pipeline end-to-end.

6. **Validation manuelle** :
   - Ajouter un tip, restart back, vérifier qu'il apparaît dans Settings.
   - Vérifier qu'il devient candidat au random après les 60s.
   - Vérifier le swap « next tip ».
   - Vérifier le sync entre 2 tabs (mark seen sur l'une → l'autre voit le
     tip retiré de la liste candidate).
   - Vérifier le reset all.
   - Vérifier qu'un tip `platform: [mobile]` n'apparaît jamais sur desktop.
   - Vérifier qu'un tip `providers_any: [codex]` n'apparaît que si codex
     est enabled.

   Pas de tests automatisés (politique projet : « no tests »).

---

## 12. Risques / questions ouvertes

- **Dépendance `pyyaml`** : ajoutée via `uv add pyyaml`. Alternative
  (mini-parser maison) jugée moins robuste pour peu de gain ; arbitré dans
  cette spec en faveur de pyyaml.
- **Emplacement de l'init du manifest** : `apps.py` `ready()` ou ASGI
  lifespan. À trancher à l'implem ; le pattern existant pour les autres
  init similaires (price sync, etc.) sert de référence.
- **Set initial de tips** : hors-spec ; à fournir séparément.
- **Hot-reload du manifest en dev** : non implémenté. Si l'expérience dev
  s'avère pénible, on pourra ajouter un watchfiles sur le dossier tips et
  re-broadcaster. Différé v2.
- **Format relatif « Seen X ago »** : réutiliser l'helper existant côté
  frontend (à localiser dans `frontend/src/utils/`). **Pas** de nouvel
  utilitaire.
- **Hatch_build.py** : à vérifier que le glob `static/frontend/**` inclut
  bien les `*.md` (pas seulement les assets buildés par Vite). Si non,
  ajustement nécessaire pour que `tips/*.md` soient présents dans le wheel.
- **Multi-tab et `nextEligibleTime`** : non synchronisé entre tabs en v1.
  Si pénible (deux tabs ouvertes affichent chacune un tip à des moments
  rapprochés), on pourra écouter `storage` events sur un clé localStorage
  partagée. Différé.
