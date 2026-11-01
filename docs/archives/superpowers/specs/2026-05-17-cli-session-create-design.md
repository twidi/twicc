# CLI Session Create — design

**Date :** 2026-05-17
**Statut :** Draft
**Scope :** Backend (mineur) + CLI (gros morceau)
**Worktree :** `feature/cli-session-create` (à créer dans `.worktrees/feature-cli-session-create`)

Document de cadrage pour une nouvelle commande CLI `twicc session create` qui
permet de spawn une session depuis le terminal, sans passer par le browser, sans
avoir à gérer l'authentification, et sans appel réseau.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Une commande `twicc session create` qui crée une session dans un projet, avec
  un prompt initial, des agent settings (modèle, effort, etc.), un preset
  éventuel, et des attachments.
- **Zéro réseau, zéro authentification** côté CLI : la CLI tourne sur la même
  machine que le serveur, elle a accès aux mêmes fichiers, et elle importe le
  code TwiCC en tant que lib pour valider les inputs avant de poser un fichier
  de demande dans `<data_dir>/sessions-pending/`.
- Un nouveau watcher côté back qui lit ces fichiers de demande et appelle le
  même code path que `_handle_send_message`, puis écrit un fichier de statut.
- La CLI poll le statut, sort proprement avec l'ID canonique de la session ou
  les erreurs.

### 0.2 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas de validation supplémentaire côté back.** Le watcher se contente
  d'appeler le même chemin que le WS consumer aujourd'hui. La CLI est la
  responsable de la validation pré-drop. Le back n'introduit pas de checks que
  le WS consumer n'a pas déjà.
- **Pas de redim d'image.** Le front fait un resize Pillow-équivalent
  in-browser. La CLI v1 envoie l'image telle quelle (limite 5 MB par fichier
  comme aujourd'hui). Le SDK gère ou échoue.
- **Pas de mode strict pour la cross-field consistency.** La CLI envoie les
  settings demandés ; si `enforce_agent_settings_consistency()` dégrade
  (`effort=max` → `xhigh`, `context_max=1M` → `200K`), c'est silencieux comme
  aujourd'hui côté WS. Pas de rejet par la CLI sur ces incohérences.
- **Pas de validation runtime du provider côté CLI.** La CLI vérifie seulement
  que le provider n'est pas dans `disabledProviders`. Si le provider n'est pas
  en `running` (e.g., `starting`), le watcher rejette avec un code clair dans
  le fichier de statut.
- **Pas de support multi-prompt / batch.** Une seule session par invocation.
- **Pas de mode "queue pour plus tard"** : si le serveur ne tourne pas, la CLI
  fail-fast avec un message clair. Pas de drop-file qui resterait pour le boot
  suivant.
- **Pas de nouveau endpoint REST / WS** ni de nouvelle surface d'authentification.

### 0.3 Vocabulaire

| Terme | Définition |
|-------|-----------|
| **Drop-file** | Fichier JSON déposé par la CLI dans `<data_dir>/sessions-pending/<request_uuid>.json` pour demander la création d'une session. |
| **Status file** | Fichier JSON écrit par le watcher dans `<data_dir>/sessions-pending/<request_uuid>.status.json` pour communiquer l'état de la demande. |
| **request_uuid** | UUID généré par la CLI, qui sert de nom de fichier et de corrélation entre drop-file et status file. **Pas** le `session_id` final (qui peut différer côté Codex). |
| **canonical_id** | L'ID définitif de la session une fois créée. Égal à `request_uuid` chez Claude Code (qui accepte le client-supplied ID), minté par le SDK chez Codex. |
| **Heartbeat file** | Fichier `<data_dir>/.server-heartbeat` mis à jour périodiquement par le serveur, utilisé par la CLI pour détecter "serveur down" pré-drop. |

---

## 1. Architecture globale

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI : importe twicc en tant que lib                             │
│                                                                  │
│  1. Résoudre data_dir via twicc.paths.get_data_dir()             │
│  2. Vérifier .server-heartbeat → fail-fast si stale              │
│  3. django.setup() (lazy, comme les autres sous-commandes)       │
│  4. Construire bootstrap-équivalent en mémoire (lecture pure) :  │
│     ├── disabledProviders ← read_synced_settings()               │
│     ├── presets ← read_agent_settings_presets(provider)          │
│     ├── categories ← Helpers.AGENT_SETTINGS_CATEGORIES           │
│     ├── choices ← Helpers.get_agent_settings_choices() [NEW]     │
│     ├── attachment_support ← Helpers.get_attachment_support()    │
│     │                                              [NEW]         │
│     └── model_registry ← Helpers.MODEL_VERSIONS                  │
│  5. Valider les inputs user (provider, settings, attachments,    │
│     prompt, project)                                             │
│  6. Résoudre / créer le Project si nécessaire                    │
│  7. Encoder les attachments (base64, MIME sniff)                 │
│  8. Écrire <data_dir>/sessions-pending/<request_uuid>.json       │
│     (atomic : .tmp + rename)                                     │
│  9. Poll <request_uuid>.status.json toutes les 100 ms            │
│  10. Print canonical_id ou erreurs, sortir                       │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Serveur ASGI (déjà en cours)                                    │
│                                                                  │
│  Task asyncio périodique : touch .server-heartbeat (toutes 5s)   │
│                                                                  │
│  PendingSessionsWatcher (watchfiles asyncio task) :              │
│  • Voit <uuid>.json apparaître                                   │
│  • Lit, parse                                                    │
│  • Écrit <uuid>.status.json = {status: "received", ...}          │
│  • Appelle create_session_from_payload(payload) [refacto]        │
│  • Sur succès → <uuid>.status.json = {status: "created", ...}    │
│  • Sur erreur connue (provider_disabled, project introuvable…)   │
│      → <uuid>.status.json = {status: "rejected", errors: [...]}  │
│  • Sur exception inattendue                                      │
│      → <uuid>.status.json = {status: "failed", error: "..."}     │
│  • Ne touche PAS aux fichiers en cas nominal : c'est la CLI qui  │
│    supprime drop-file + status file après avoir lu le statut.    │
│    (Garde-fou : au boot, le watcher scanne le dossier et         │
│    nettoie les fichiers résiduels d'une CLI plantée — détails    │
│    en section 5.5.)                                              │
│  • Les sessions apparaissent côté front via le file watcher      │
│    JSONL existant (pas de broadcast WS nouveau)                  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.1 Pourquoi cette architecture

Trois itérations dans le brainstorming ont éliminé :
1. **CLI standalone qui démarre Django + watcher + manager** : trop lourd, race
   condition avec le serveur en cours d'exécution.
2. **CLI client WS + auth password** : oblige à gérer un protocole réseau et
   l'authentification dans la CLI.
3. **CLI client HTTP + token local + WS pour `send_message`** : token local
   propre mais double surface (HTTP pour bootstrap + WS pour création).
4. **CLI client HTTP + token local + drop-file pour création** : token local
   utile, mais inutile si la CLI peut lire les mêmes fichiers que le serveur.

L'option retenue (CLI = lib-consumer, drop-file pour création) est la plus
simple : la CLI n'a aucune surface réseau, n'a pas besoin d'authentification, et
réutilise les mêmes constants Python que le bootstrap HTTP.

---

## 2. Surface CLI

### 2.1 Commande

```
twicc create-session [OPTIONS] PROMPT
```

**Top-level command** (pas sous `session_app`). Raison : le `session_app`
existant a un callback avec un `session_id` positional **requis** pour
l'inspection (`twicc session <id>`, `twicc session <id> content`, etc.).
Ajouter une sous-commande `create` créerait un conflit de parsing Typer
(le mot "create" serait interprété comme `session_id`). Une top-level
command est plus simple et sémantiquement cohérente avec `usage`, `projects`,
`sessions`, etc. qui sont également top-level.

### 2.2 Arguments et options

| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `PROMPT` (positional) | str | Oui | — | Soit du texte brut, soit un path absolu vers un fichier dont le contenu UTF-8 sert de prompt. Heuristique : `os.path.isfile(value)` → fichier, sinon texte. |
| `--project` | str | Non | cwd | Soit un project_id canonique (avec ou sans le `-` initial), soit un path (absolu ou relatif au cwd). Heuristique : `os.path.isdir(value)` → traité comme path, sinon comme ID (recherche par `value` puis par `-value`). Si path et projet inexistant → on crée le projet. Si project_id inexistant → erreur. Si non fourni → équivalent à `--project .` (le cwd). |
| `--provider` | choice | Oui | — | `claude_code` ou `codex`. Doit ne pas être dans `disabledProviders`. |
| `--preset` | str | Non | — | Nom d'un preset enregistré pour ce provider. Les options individuelles ci-dessous écrasent les valeurs du preset. |
| `--model` | str | Non | — | Alias modèle (ex. `opus`, `sonnet`, `opus-4.7`, `gpt`, `gpt-5.4`). |
| `--effort` | choice | Non | — | `low`, `medium`, `high`, `xhigh`, `max`. Claude Code uniquement pour `xhigh`/`max` (gated par modèle, dégradation silencieuse côté back). |
| `--permission-mode` | str | Non | — | Claude Code : `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex : `read_only`, `strict`, `auto`, `autonomous`, `yolo`. |
| `--thinking / --no-thinking` | bool ou `None` | Non | `None` | Claude Code uniquement. Déclaré en Typer comme `typer.Option(None, "--thinking/--no-thinking")` pour distinguer "non passé" (`None`, le preset n'est pas écrasé) de `False` (`--no-thinking`, écrase explicitement). |
| `--claude-in-chrome / --no-claude-in-chrome` | bool ou `None` | Non | `None` | Claude Code uniquement. Même sémantique tri-state. |
| `--context-max` | int | Non | — | Claude Code : `200000` ou `1000000`. Codex : `272000` (défaut). |
| `--title` | str | Non | — | Titre custom de la session (max 200 chars). Sinon le SDK / les heuristiques internes décident. |
| `--attach` | path (répétable) | Non | — | Chemin absolu ou relatif vers un fichier à attacher. Validé par MIME / taille / count / provider. |
| `--timeout` | int (secondes) | Non | 30 | Timeout pour le polling du status. |
| `--no-color` | flag | Non | False | Désactive les couleurs dans la sortie. |
| `--json` | flag | Non | False | Sortie au format JSON (parseable par scripts). |

### 2.3 Exemples d'usage

```bash
# Session simple (projet = cwd par défaut)
twicc create-session \
  --provider claude_code \
  "Run the tests and fix the errors"

# Projet explicite (path)
twicc create-session \
  --project /home/twidi/dev/twicc-poc \
  --provider claude_code \
  "Run the tests and fix the errors"

# Avec preset + override
twicc create-session \
  --project home-twidi-dev-twicc-poc \
  --provider claude_code \
  --preset "deep think" \
  --model opus-4.7 \
  "Do a security audit"

# Avec attachments
twicc create-session \
  --project /home/twidi/dev/foo \
  --provider claude_code \
  --attach /home/twidi/screenshot.png \
  --attach /home/twidi/report.pdf \
  "What do you think about these results?"

# Path relatif (résolu par rapport au cwd)
twicc create-session \
  --project ./my-feature-branch \
  --provider claude_code \
  "Refactor in TDD mode"

# Prompt depuis fichier (path absolu ou relatif)
twicc create-session \
  --project home-twidi-dev-foo \
  --provider claude_code \
  /home/twidi/prompts/security-audit.md

# Output JSON pour scripts
twicc create-session --json \
  --provider claude_code \
  "Hello"
# → {"status": "created", "session_id": "...", "provider": "claude_code"}
```

### 2.4 Sortie

**Note : tout le wording user-facing est en anglais (CLAUDE.md project rule)**.
Les exemples ci-dessous sont les chaînes finales tapées par l'utilisateur.

#### Format texte (défaut)

```
✓ Heartbeat OK (last seen 2s ago)
✓ Project "home-twidi-dev-foo" (existing)
✓ Provider claude_code enabled
✓ Settings validated
✓ Attachments validated (2 files, 1.3 MB total)
→ Request submitted (request_uuid: 9f7a...)
  Received by server (47 ms)
✓ Session created: 4d3c2b1a-...-...
```

En cas d'erreur côté CLI (validation) :

```
✗ Validation error:
  - effort: invalid value "ultra" for claude_code
    expected: low, medium, high, xhigh, max
  - attachments[1]: report.pdf is 8.2 MB, max 5 MB
  - attachments[2]: report.docx, type application/vnd...
    not supported by claude_code (accepted: image/png, image/jpeg,
    image/gif, image/webp, application/pdf, text/plain)
```

En cas d'erreur côté back (status `rejected` ou `failed`) :

```
✓ Heartbeat OK
✓ Project ...
→ Request submitted (request_uuid: 9f7a...)
  Received by server (47 ms)
✗ Rejected by server:
  - provider_disabled: claude_code is not ready yet (state: starting)
```

#### Format JSON (`--json`)

Toujours un objet, jamais une string. Structure :

```json
{
  "status": "created" | "rejected" | "failed" | "validation_error" | "server_down" | "timeout",
  "session_id": "...",                 // si status == "created"
  "provider": "...",                   // si status == "created"
  "project_id": "...",                 // si status == "created"
  "request_uuid": "...",               // toujours présent
  "errors": [                          // si status != "created"
    {"field": "effort", "code": "invalid_choice", "message": "..."}
  ]
}
```

### 2.5 Codes de retour

| Code | Signification |
|------|---------------|
| 0 | Session créée avec succès |
| 1 | Erreur de validation côté CLI (mauvais argument, attachment invalide…) |
| 2 | Serveur down (heartbeat stale ou absent) |
| 3 | Serveur a rejeté la demande (rejected) |
| 4 | Erreur inattendue côté serveur (failed) |
| 5 | Timeout en attente du statut |
| 64 | Erreur d'usage (mauvais flag, etc., géré par Typer) |

---

## 3. Validation côté CLI

Toute la validation arrive **avant** le drop-file. Si une seule règle échoue,
on n'écrit pas dans `sessions-pending/`. L'utilisateur voit l'ensemble des
erreurs (pas seulement la première : on collecte tout, comme un linter).

### 3.1 Provider

- Le provider passé via `--provider` doit faire partie des providers
  enregistrés : `{p for p, _ in get_provider_helpers_registry().items()}`.
  Note : `PROVIDER_HELPERS` est une `ClassVar` annotée mais assignée en
  `self.PROVIDER_HELPERS` dans `__init__`, donc l'accès en classe lève
  `AttributeError`. Il faut passer par l'instance via
  `get_provider_helpers_registry()`.
- Le provider ne doit pas être dans `read_synced_settings().get("disabledProviders", [])`.
- Si la clé `disabledProviders` est **absente** des synced settings (premier
  boot jamais effectué), on échoue avec un message clair : "TwiCC n'a jamais
  été démarré, lance `twicc` une fois pour activer les providers."

### 3.2 Agent settings (merge preset + overrides)

Ordre d'application :
1. Initialiser une `AgentSettings(None, None, None, None, None, None)` (six `None`).
   **La CLI ne définit aucune valeur par défaut elle-même** ; elle laisse le
   back appliquer les synced defaults pour chaque champ `None`.
2. Si `--preset <name>` est fourni : charger le preset via
   `read_agent_settings_presets(provider)`, retrouver l'entrée par `name`
   (case-sensitive). Erreur si introuvable. Appliquer son contenu : `model` →
   `selected_model`, `thinking` → `thinking_enabled`, le reste 1:1. Les
   champs absents du preset restent `None`.
3. Pour chaque option CLI fournie (`--model`, `--effort`, etc.), écraser le
   champ correspondant. **Seuls** les champs explicitement passés en ligne de
   commande sont écrasés ; les autres restent à ce qu'a mis le preset (ou
   `None` si pas de preset).
4. Le résultat (avec les `None` résiduels) est envoyé dans le drop-file.

#### 3.2.1 Cas d'usage (sémantique attendue)

| Invocation | `effort` envoyé | `selected_model` envoyé | ... |
|---|---|---|---|
| `twicc create-session ... "x"` (rien) | `None` → default back | `None` → default back | `None` → default back |
| `... --effort high "x"` | `"high"` | `None` → default back | `None` → default back |
| `... --preset deepthink "x"` (preset : `{effort: "max", model: "opus-4.7"}`) | `"max"` (du preset) | `"opus-4.7"` (du preset) | `None` → default back |
| `... --preset deepthink --effort low "x"` | `"low"` (flag écrase preset) | `"opus-4.7"` (du preset) | `None` → default back |

Règle d'or : **un champ qui n'est ni dans le preset ni explicitement fourni
en flag est envoyé à `None`**. C'est le back qui résout `None` → synced
default au moment de l'application (cf. `helpers.resolve_agent_settings()`).
La CLI ne fait pas de fallback elle-même.

Validation par champ :
- Chaque champ non-`None` doit faire partie des champs déclarés par le provider
  (union de `AGENT_SETTINGS_CATEGORIES.values()`). Si on tape
  `--claude-in-chrome` sur Codex, erreur immédiate.
- Chaque champ non-`None` doit avoir une valeur acceptée par
  `Helpers.get_agent_settings_choices()[field]` :
  - `selected_model` : doit exister dans `MODEL_VERSIONS` pour ce provider.
  - `effort` : doit être dans la liste statique du provider.
  - `permission_mode` : idem.
  - `thinking_enabled`, `claude_in_chrome` : booléens.
  - `context_max` : entier dans la liste autorisée pour ce provider
    (Claude Code : `[200_000, 1_000_000]`, Codex : `[272_000]`).

**Pas** de cross-field check : conformément au cadrage, on laisse la dégradation
silencieuse côté back.

### 3.3 Project

L'argument `--project` est **optionnel**. S'il n'est pas fourni, on prend le
cwd comme s'il avait été passé via `--project .`.

Heuristique : si la valeur est un dossier existant (`os.path.isdir(value)`),
on la traite comme un path ; sinon comme un project_id canonique. Pour
l'ID, on accepte aussi la forme **sans le `-` initial** (sucre syntaxique
qui évite à l'user de devoir taper `--project=-home-twidi-dev-foo` à cause
du `-` initial qui ressemble à un flag).

```python
import argparse
project_arg: str | None = ...  # la valeur de --project, ou None si absent

if project_arg is None:
    project_arg = os.getcwd()  # fallback implicite sur le cwd

if os.path.isdir(project_arg):
    # Path (absolu ou relatif au cwd) — on résout en absolu pour le mapping.
    resolved = os.path.realpath(project_arg)
    project_id = path_to_project_id(resolved)
    project, created = Project.objects.get_or_create(
        id=project_id,
        defaults={"directory": resolved},
    )
    if created:
        # broadcast project_added pour que le front se rafraîchisse
        await broadcast_project_added(project)
    # Cas où le projet existe mais directory était NULL (créé par le watcher
    # Claude Code sans backfill) : on patch directory.
    if not project.directory:
        project.directory = resolved
        project.save(update_fields=["directory"])
else:
    # ID canonique attendu (ou un path qui n'existe pas → traité comme ID)
    project = None
    for candidate_id in (project_arg, "-" + project_arg):
        try:
            project = Project.objects.get(id=candidate_id)
            break
        except Project.DoesNotExist:
            continue
    if project is None:
        raise ValidationError(
            f"--project: {project_arg!r} is neither an existing directory "
            f"nor a known project_id (tried also with leading '-')."
        )
    if not project.directory:
        raise ValidationError(
            f"--project: project {project.id!r} exists but has no directory set"
        )
```

**Pourquoi accepter l'ID sans le `-` initial** : tous les project_id générés
par `path_to_project_id()` commencent par `-` (le `/` initial du path absolu
est remplacé par `-` via la regex `re.sub(r'[^a-zA-Z0-9]', '-', path)`).
Or, un argument shell qui commence par `-` est interprété comme un flag,
ce qui oblige l'user à utiliser `--project=-home-twidi-dev-foo` ou
`--project -- -home-twidi-dev-foo`. Permettre `--project home-twidi-dev-foo`
(sans le tiret initial) lève cette friction. La résolution essaye d'abord
la valeur telle quelle, puis avec un `-` préfixé. Le pattern est
emprunté à d'autres CLI qui font la même chose.

**Note sur l'auto-create silencieux** : le REST endpoint `POST /api/projects/`
(utilisé par le front) demande à l'utilisateur de confirmer si le directory
n'existe pas via `create_directory: true`. La CLI **ne reproduit pas** cette
confirmation : on exige un directory **déjà existant** sur disque
(`os.path.isdir` strict). C'est la responsabilité de l'utilisateur d'avoir
créé le dossier avant d'invoquer la CLI. Pas de `--create-directory` v1.

### 3.4 Attachments

Pour chaque `--attach <path>` :
- Le path doit exister et être un fichier régulier.
- Détecter le MIME via magic bytes (signature). Stratégie minimaliste sans
  dépendance : check explicite pour PNG, JPEG, GIF, WebP, PDF ; tout le reste
  est traité comme `text/plain` si décodable UTF-8, sinon refus.
- Vérifier le MIME contre
  `Helpers.get_attachment_support()["accepted_mime_types"]` du provider choisi.
- Vérifier la taille ≤ `max_bytes_per_file` (5 MB par défaut).
- Vérifier que le total des attachments ≤ `max_total_bytes` (32 MB).
- Vérifier le nombre total ≤ `max_files_per_message` (100).
- Encoder le contenu :
  - Images / PDF : base64 du contenu binaire.
  - Plain text : UTF-8, brut (sans base64).
- Construire les blocks au format SDK :

```python
# Images
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}

# PDFs
{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}}

# Plain text
{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "raw text content"}}
```

Trier en deux listes : `images[]` et `documents[]`, dans l'ordre où elles ont
été passées en ligne de commande.

### 3.5 Prompt

```python
prompt_arg: str = ...  # le positional

if os.path.isfile(prompt_arg):
    with open(prompt_arg, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        raise ValidationError(f"prompt: fichier {prompt_arg!r} vide")
else:
    text = prompt_arg
    if not text.strip():
        raise ValidationError("prompt: vide")
```

Pas de "fallback texte si le fichier existe mais est mal encodé" : si
`UnicodeDecodeError`, on lève une `ValidationError` avec un message clair.

### 3.6 Heartbeat (pré-drop)

```python
heartbeat = data_dir / ".server-heartbeat"
if not heartbeat.exists():
    raise ServerDownError(...)
age = time.time() - heartbeat.stat().st_mtime
if age > 15:  # configurable, défaut 15s = 3× la période d'écriture
    raise ServerDownError(f"Heartbeat stale ({int(age)}s ago)")
```

Si l'heartbeat est OK, on procède au drop-file.

---

## 4. Format du drop-file

### 4.1 Nom du fichier

`<data_dir>/sessions-pending/<request_uuid>.json`

- `<data_dir>` résolu via `twicc.paths.get_data_dir()`.
- `<request_uuid>` est un UUID v4 généré par la CLI.
- L'écriture est atomique : la CLI écrit `<request_uuid>.json.tmp` puis fait
  `os.rename()` vers `<request_uuid>.json`. `watchfiles` voit le rename comme
  une création atomique.

### 4.2 Schema

```json
{
  "version": 1,
  "request_uuid": "9f7a-...-...",
  "submitted_at": "2026-05-17T14:23:00.123Z",
  "submitter": {
    "user": "twidi",
    "hostname": "morpheus",
    "pid": 12345
  },
  "payload": {
    "session_id": "9f7a-...-...",
    "project_id": "home-twidi-dev-foo",
    "provider": "claude_code",
    "text": "Le contenu du prompt résolu, ici en clair",
    "title": "Titre custom ou null",
    "images": [
      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
    ],
    "documents": [
      {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}}
    ],
    "agent_settings": {
      "permission_mode": null,
      "selected_model": "opus-4.7",
      "effort": "high",
      "thinking_enabled": null,
      "claude_in_chrome": null,
      "context_max": null
    }
  }
}
```

Notes :
- `request_uuid` et `payload.session_id` sont identiques. La CLI les génère et
  les utilise tels quels. Chez Claude Code, ce sera aussi le canonical_id ;
  chez Codex, le canonical_id sera minté par le SDK et différent.
- `payload` reprend exactement le shape que le WS consumer reçoit en
  `_handle_send_message`, **sans** le `type: "send_message"` (implicite ici).
- `agent_settings` est un sous-objet (pas à plat) pour rendre clair le bundle
  fermé. Le watcher l'aplatit avant d'appeler le service de création.
- `submitter` est purement informationnel (pour debug / logs serveur). Le
  watcher n'agit pas dessus. Pas de `cli_version` : la CLI et le serveur sont
  livrés dans la même wheel, donc même version par construction.

### 4.3 Tailles attendues

Pour une session sans attachment : ~500 bytes. Pour une session avec un PDF
de 5 MB encodé base64 : ~6.7 MB. Bien dans les limites raisonnables d'un JSON
sur disque local. `orjson` côté CLI et back pour la sérialisation rapide.

---

## 5. Format du status file

### 5.1 Nom du fichier

`<data_dir>/sessions-pending/<request_uuid>.status.json`

Le watcher l'écrit en plusieurs étapes. Écriture atomique pareil (`.tmp` +
rename) pour que la CLI ne lise jamais un JSON tronqué.

### 5.2 Cycle de vie

Le fichier est créé puis mis à jour 1 à 3 fois :

1. **`received`** — dès que le watcher voit le drop-file et a fini de le parser.
   Indique que la demande a été prise en compte.
2. État final, **un seul de ces 3** :
   - **`created`** — la session a été créée avec succès, le `session_id`
     canonique est connu.
   - **`rejected`** — une erreur connue (provider disabled, projet introuvable,
     payload mal formé, etc.) empêche la création. Détails dans `errors`.
   - **`failed`** — une exception inattendue est survenue côté serveur.
     Détails dans `error` (string).

### 5.3 Schemas

```json
// 1. received
{
  "status": "received",
  "request_uuid": "9f7a-...",
  "received_at": "2026-05-17T14:23:00.150Z"
}

// 2a. created (état final, succès)
{
  "status": "created",
  "request_uuid": "9f7a-...",
  "received_at": "2026-05-17T14:23:00.150Z",
  "created_at": "2026-05-17T14:23:01.020Z",
  "session_id": "<canonical_id>",
  "provider": "claude_code",
  "project_id": "home-twidi-dev-foo"
}

// 2b. rejected (état final, erreur connue)
{
  "status": "rejected",
  "request_uuid": "9f7a-...",
  "received_at": "2026-05-17T14:23:00.150Z",
  "rejected_at": "2026-05-17T14:23:00.180Z",
  "errors": [
    {
      "field": "provider",
      "code": "provider_disabled",
      "message": "Provider claude_code is disabled or not running"
    }
  ]
}

// 2c. failed (état final, erreur inattendue)
{
  "status": "failed",
  "request_uuid": "9f7a-...",
  "received_at": "2026-05-17T14:23:00.150Z",
  "failed_at": "2026-05-17T14:23:01.500Z",
  "error": "RuntimeError: subprocess died unexpectedly: ..."
}
```

### 5.4 Codes d'erreur (pour `rejected`)

Stables, machine-friendly :

| Code | Sens |
|------|------|
| `provider_disabled` | Le provider est désactivé ou pas encore en `running`. |
| `project_not_found` | `project_id` inexistant en DB (ne devrait pas arriver si la CLI fait bien son boulot, mais defensive). |
| `project_no_directory` | Projet existe mais n'a pas de `directory` set. |
| `invalid_title` | Titre vide ou trop long. |
| `empty_text` | `text` vide pour une session nouvelle. |
| `manager_busy` | L'agent manager refuse de créer la session (déjà une en cours pour ce session_id, etc.). |
| `unknown_provider` | Provider inconnu dans le payload. |

Tout autre cas → `failed` avec le message libre.

### 5.5 Nettoyage

**Principe : la CLI nettoie ses propres fichiers.** Qui dépose nettoie, dans
le cas nominal.

**Cas nominal (CLI tourne jusqu'au bout)** :
- Dès que la CLI observe un statut final (`created`, `rejected`, ou
  `failed`), elle supprime **les deux fichiers** :
  `<request_uuid>.json` (le drop-file) **et** `<request_uuid>.status.json`.
- Le watcher ne supprime **rien** en cas de traitement normal — il se
  contente d'écrire et de mettre à jour le status file ; la CLI fait le
  cleanup.
- Suppressions atomiques avec `missing_ok=True` (si le watcher a déjà fait
  le ménage au boot pour un cas dégénéré).

**Garde-fous côté watcher** (cas dégénérés : CLI crashée, serveur redémarré
pendant le traitement) :
- Pas de scan périodique pendant la vie du serveur.
- Au **démarrage** du `PendingSessionsWatcher`, scan unique du dossier, basé
  sur la **co-présence** drop / status (jamais sur des timestamps) :
  - **Drop-file sans status file associé** → la CLI a fait sa demande mais
    le watcher n'a pas encore traité (le serveur a redémarré avant). On
    crée la session normalement, peu importe l'ancienneté du drop. Un
    drop déposé hier soir avec un crash serveur sera quand même traité au
    matin.
  - **Drop-file avec status file associé** → la CLI a planté avant de
    pouvoir supprimer les deux fichiers. La session a déjà été créée /
    rejetée / failed. On supprime les deux fichiers. Pas de re-traitement.
  - **Status file orphelin (sans drop-file)** → la CLI a planté entre le
    moment où le watcher d'un boot précédent a, dans le cadre du second
    cas ci-dessus, supprimé le drop-file mais pas le status. Suppression
    silencieuse.
- **Aucune notion de TTL / âge** dans cette logique. La co-présence
  drop+status est la signature d'une session déjà traitée ; l'absence du
  status la signature d'une demande à traiter.
- Pas de scan périodique en cours d'exécution : si une CLI crashe pendant
  l'exécution du serveur, ses fichiers traîneront jusqu'au prochain
  redémarrage du watcher. Acceptable car le data_dir reste petit (un drop
  + status = ~quelques KB sans attachments, ~quelques MB avec).

---

## 6. Changements back

### 6.1 Nouveaux helpers Python (`agent_settings_choices` + `attachment_support`)

Objectif : déplacer les constantes JS de
`frontend/src/providers/*/constants.js` vers Python, pour qu'elles soient
accessibles depuis la CLI **et** depuis le bootstrap HTTP (suppression de la
duplication côté front).

#### `Helpers.get_agent_settings_choices() -> dict[str, list]`

Sur `BaseProviderHelpers` (méthode abstraite), implémenté par chaque provider :

```python
# claude_code/helpers.py
AGENT_SETTINGS_CHOICES = {
    "effort": ["low", "medium", "high", "xhigh", "max"],
    "permission_mode": ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"],
    "thinking_enabled": [True, False],
    "context_max": [200_000, 1_000_000],
    "claude_in_chrome": [True, False],
    # selected_model: pas listé ici — vient de MODEL_VERSIONS via model_registry
}

# codex/helpers.py
AGENT_SETTINGS_CHOICES = {
    "effort": ["low", "medium", "high", "xhigh"],
    "permission_mode": ["read_only", "strict", "auto", "autonomous", "yolo"],
    "context_max": [272_000],
}
```

Exposé dans `get_bootstrap_data()` → ajout de la clé `agent_settings_choices`
dans la map `providers[provider]`. Le front consomme la même source et supprime
les constants JS.

#### `Helpers.get_attachment_support() -> dict`

```python
# claude_code/helpers.py
ATTACHMENT_SUPPORT = {
    "images": True,
    "documents": True,
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "application/pdf", "text/plain",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}

# codex/helpers.py
ATTACHMENT_SUPPORT = {
    "images": True,
    "documents": False,  # documents droppés au manager level chez Codex
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}
```

Idem : exposé dans `get_bootstrap_data()`, front consomme et supprime la
duplication.

### 6.2 Service extrait : `create_session_from_payload(payload)`

Aujourd'hui, `_handle_send_message` dans `src/twicc/asgi.py` mélange validation,
résolution, broadcast et orchestration. On extrait le **cœur** en un nouveau
module `src/twicc/core/services/session_creation.py` :

```python
class SessionCreationResult(NamedTuple):
    success: bool
    session_id: str | None      # canonical_id (différent du draft chez Codex)
    provider: str | None
    project_id: str | None
    errors: list[dict] | None   # liste de {field, code, message}

async def create_session_from_payload(payload: dict) -> SessionCreationResult:
    """
    Code path unifié pour la création d'une session.

    payload : dict avec les mêmes clés que ce que reçoit _handle_send_message.

    Retourne un SessionCreationResult.

    Aucun broadcast WS supplémentaire n'est émis par ce service au-delà de
    ce que font déjà les managers et watchers existants (les sessions
    apparaissent côté front via le file watcher JSONL standard, comme
    aujourd'hui). Pas de broadcast `session_added` à inventer.
    """
```

Appelé par :
- `_handle_send_message` (WS consumer) — un léger refacto pour ne plus faire
  inline tout le travail, juste appeler le service et émettre éventuellement
  une `error` dédiée à la connexion.
- `PendingSessionsWatcher` (nouveau) — appelle le service et écrit le status
  file en fonction du résultat.

Le service ne lève **pas** d'exception pour les erreurs métier (provider
disabled, project not found…) : il retourne un `SessionCreationResult` avec
`success=False` et `errors=[{field, code, message}, ...]`. Les exceptions
inattendues remontent normalement et sont attrapées par l'appelant (le
watcher les transforme en `status: failed`).

### 6.2.1 Récupération du `canonical_id` Codex

Aujourd'hui, `CodexAgentManager.create_session()` (dans
`providers/codex/agent/manager.py`) **retourne `None`** ; le canonical_id minté
par le SDK est accessible uniquement via `agent.session_id` (un attribut
d'instance interne au manager). Le WS consumer ne le récupère jamais : il
broadcaste `session_bound` à la place, et le front réconcilie son draft id.

Pour que `create_session_from_payload()` puisse retourner ce canonical_id,
il faut **modifier la signature de `create_session()`** dans les deux managers
(`ClaudeCodeAgentManager` et `CodexAgentManager`) pour qu'elle retourne le
canonical_id (`str`) au lieu de `None`. Côté Claude Code, c'est identique au
draft id. Côté Codex, c'est l'ID minté par `thread_start()`.

Le `session_bound` broadcast existant reste en place (consommé par le front).
Le WS consumer est légèrement adapté : il appelle le service, ignore la valeur
de retour (le broadcast suffit pour le front), continue comme avant.

C'est une **modification chirurgicale** : 2 fichiers (un par manager), pas
plus de 10 lignes de code.

### 6.3 Heartbeat task

Nouveau module `src/twicc/heartbeat.py`. Task asyncio démarrée au boot du
serveur, **après** `migrate` (rien ne doit tourner avant `migrate`,
contrainte projet).

**Ordre de démarrage retenu :**

```python
# cli/run.py — esquisse
call_command("migrate", ...)              # bloquant, doit terminer en premier
# ... reste du boot des orchestrators et watchers ...
asyncio.ensure_future(heartbeat_loop())   # APRÈS migrate
asyncio.ensure_future(pending_sessions_watcher.start())
uvicorn.run(...)
```

```python
async def heartbeat_loop() -> None:
    path = get_data_dir() / ".server-heartbeat"
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            path.touch(exist_ok=True)
            os.chmod(path, 0o600)  # idempotent
        except Exception as e:
            logger.warning("heartbeat write failed: %s", e)
        await asyncio.sleep(5)
```

Le fichier est vide (seul son mtime compte côté CLI). Mode 0o600 (owner-only)
appliqué après le touch.

**Conséquence pour la CLI pendant le boot** : si une CLI est lancée pendant
le `migrate` ou avant que la heartbeat task ne soit démarrée, elle verra
l'heartbeat absent ou stale et fail-fast avec un message clair (cf.
section 10). Acceptable : c'est à l'utilisateur d'attendre la fin du
démarrage du serveur (visible dans les logs) avant d'invoquer la CLI. Le
wording de l'erreur mentionne explicitement le cas "en cours de démarrage".

Côté CLI, la tolérance est 15 secondes (3× la période d'écriture). Permet
d'absorber un pic de charge ou un GC long sans faux positif une fois le
serveur en régime nominal.

### 6.4 `PendingSessionsWatcher`

Nouveau module `src/twicc/pending_sessions_watcher.py`. Suit le pattern des
autres watchers (basé sur `watchfiles`).

**Important — ordre `_scan_existing` vs `awatch`** : `watchfiles.awatch` peut
manquer des fichiers déposés *avant* qu'il ne commence à observer. Pour fermer
cette race, on doit (a) démarrer `awatch` **d'abord**, puis (b) scanner les
fichiers déjà présents. Tout fichier vu deux fois (par scan ET par
notification) est protégé par un dedup interne (set de UUIDs en cours de
traitement).

**Sémantique au boot** (basée sur la co-présence drop / status, jamais sur
des timestamps — cf. section 5.5) :
- Drop sans status → on traite (création de session normale).
- Drop + status → on supprime les deux (CLI a crashé avant cleanup).
- Status seul → on supprime (orphelin).

```python
class PendingSessionsWatcher:
    def __init__(self) -> None:
        self.directory = get_data_dir() / "sessions-pending"
        self._in_flight: set[str] = set()

    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Démarrer awatch d'abord pour éviter la race
        watch_task = asyncio.ensure_future(self._watch_loop())
        await self._scan_existing()
        await self._cleanup_orphan_status_files()
        await watch_task  # ne devrait jamais terminer

    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory):
            for change_type, path in changes:
                p = Path(path)
                if change_type == Change.added and p.suffix == ".json" \
                   and not p.name.endswith(".status.json") \
                   and not p.name.endswith(".tmp") \
                   and p.stem not in self._in_flight:
                    asyncio.ensure_future(self._process_file(p))

    async def _scan_existing(self) -> None:
        for p in sorted(self.directory.glob("*.json")):
            if p.name.endswith(".status.json") or p.name.endswith(".tmp"):
                continue
            if p.stem in self._in_flight:
                continue
            status_path = self.directory / f"{p.stem}.status.json"
            if status_path.exists():
                # CLI crashed before deleting both files after seeing the
                # status. Session was already created/rejected, just clean up.
                p.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
            else:
                # Drop-file orphaned by a server restart — create session
                # normally, no timing check.
                asyncio.ensure_future(self._process_file(p))

    async def _cleanup_orphan_status_files(self) -> None:
        for p in sorted(self.directory.glob("*.status.json")):
            request_uuid = p.name[:-len(".status.json")]
            drop_path = self.directory / f"{request_uuid}.json"
            if not drop_path.exists():
                p.unlink(missing_ok=True)

    async def _process_file(self, path: Path) -> None:
        request_uuid = path.stem
        self._in_flight.add(request_uuid)
        try:
            try:
                # orjson.loads est CPU-bound sur gros payloads (attachments
                # base64). On le déporte sur le pool de threads pour ne pas
                # bloquer l'event loop.
                content = await asyncio.to_thread(path.read_bytes)
                data = await asyncio.to_thread(orjson.loads, content)
            except Exception as e:
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"Could not parse drop-file: {e}",
                })
                return  # CLI supprimera drop+status

            await self._write_status(request_uuid, {"status": "received"})

            try:
                result = await create_session_from_payload(data["payload"])
            except Exception as e:
                logger.exception("PendingSessionsWatcher: unexpected error for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"{type(e).__name__}: {e}",
                })
                return  # CLI supprimera drop+status

            if result.success:
                await self._write_status(request_uuid, {
                    "status": "created",
                    "session_id": result.session_id,
                    "provider": result.provider,
                    "project_id": result.project_id,
                })
            else:
                await self._write_status(request_uuid, {
                    "status": "rejected",
                    "errors": result.errors,
                })
            # Pas de unlink ici : c'est la CLI qui nettoie son drop-file
            # une fois qu'elle a observé le status final. Le watcher ne
            # supprime que dans le scan au boot pour les fichiers résiduels
            # d'une CLI plantée (cf. _scan_existing).
        finally:
            self._in_flight.discard(request_uuid)
```

Démarré au boot dans `cli/run.py` au même moment que les autres
orchestrators / watchers (avant l'`uvicorn.run`).

**Observabilité côté serveur** : chaque étape clé du `_process_file` log via
le module logging standard avec le préfixe `[PendingSessionsWatcher]` et le
`request_uuid` :
- `seen drop-file <uuid>`
- `parsed payload, calling service`
- `result: success/error, session_id=<id>` ou `result: rejected, errors=...`
- `failed: <exception>`

Niveau `INFO` pour les succès, `WARNING` pour rejected, `ERROR` pour failed.
Visible dans `<data_dir>/logs/backend.log`.

### 6.5 Permissions filesystem

- `<data_dir>/sessions-pending/` créé en `0o700` (owner-only).
- `<data_dir>/.server-heartbeat` créé en `0o600`.

C'est ce qui sert d'authentification implicite : seul l'user qui a démarré
TwiCC peut écrire dans ce dossier, donc seul lui peut faire dropper des
demandes de session.

**Note Windows** : sur Windows, `chmod` et les bits de permission Unix n'ont
pas de sémantique équivalente — la création utilise les ACL par défaut du
dossier parent. Le modèle de sécurité de la CLI repose alors sur le fait que
la machine est mono-utilisateur (ce qui est le cas standard pour un poste
de dev avec TwiCC installé via uvx/uv). Si un autre user du même OS Windows
a accès au `data_dir`, il peut dropper des sessions au nom de l'utilisateur.
Documenté comme limitation connue. Pas de mitigation v1 (ajouter une vraie
ACL setup est hors scope).

---

## 7. Layout des modules CLI

```
src/twicc/cli/
├── __init__.py                  (EXISTANT — registre @app.command("create-session"))
├── session.py                   (EXISTANT — sub-app session, inchangé)
└── create_session/              (NOUVEAU module)
    ├── __init__.py
    ├── command.py               (Typer @app.command("create-session"))
    ├── discovery.py             (data_dir + heartbeat check)
    ├── bootstrap_local.py       (charge la même donnée que /api/bootstrap/)
    ├── validation.py            (orchestrate toute la validation)
    ├── presets.py               (preset lookup + merge avec overrides)
    ├── attachments.py           (MIME sniff, base64, build blocks SDK)
    ├── project.py               (résoudre / créer le Project)
    ├── prompt.py                (resolve text vs file)
    ├── drop_file.py             (atomic write du drop-file)
    ├── polling.py               (read status file en boucle)
    └── output.py                (formatting texte / JSON)
```

### 7.1 Découpage par fichier

| Fichier | Responsabilité | LOC approximatives |
|---------|----------------|--------------------|
| `command.py` | Décoration Typer, parsing des options, orchestration top-level. | ~120 |
| `discovery.py` | `get_data_dir()`, check heartbeat. | ~40 |
| `bootstrap_local.py` | Construire en mémoire la même structure que `get_bootstrap_data()` pour le provider sélectionné. | ~80 |
| `validation.py` | Orchestre tous les checks (provider, settings, prompt, project, attachments). Collecte les `ValidationError`. | ~150 |
| `presets.py` | Lookup preset par nom, merge avec overrides CLI. | ~60 |
| `attachments.py` | MIME sniff, validation taille / count / total, encoding base64, build SDK blocks. | ~150 |
| `project.py` | Résoudre arg `--project` (path vs id), `get_or_create`, broadcast `project_added`. | ~80 |
| `prompt.py` | Resolve text vs file, validation non-vide. | ~30 |
| `drop_file.py` | Atomic write + ensure directory exists. | ~50 |
| `polling.py` | Boucle `read status file` toutes les 100 ms, gestion timeout, parsing JSON. | ~80 |
| `output.py` | Formatter texte (avec ou sans couleur) et JSON. | ~80 |
| **Total** | | **~920 lignes** |

### 7.2 Dépendances Python

- `typer` (déjà présent)
- `orjson` (déjà présent)
- `httpx` : **non nécessaire** (pas de réseau)
- `watchfiles` : **non nécessaire côté CLI** (utilisé côté back uniquement)
- `Pillow` : pas en v1 (pas de resize)
- `python-magic` : pas utilisé, détection MIME manuelle sur les ~6 types acceptés

Aucune nouvelle dépendance à ajouter au `pyproject.toml`.

---

## 8. Concurrence et race conditions

### 8.1 Lecture pendant écriture

La CLI écrit avec `.tmp + rename` ; le watcher reçoit l'event uniquement après
le rename. Pas de risque de lire un JSON tronqué. Idem côté serveur pour
l'écriture du status file (lu par la CLI).

### 8.2 Plusieurs CLI en parallèle

Chaque CLI a son propre `request_uuid` ; pas de collision possible.

### 8.3 Serveur qui ne tourne pas

Bloqué par le check heartbeat pré-drop. Si malgré ça le serveur s'arrête entre
le check et le drop, la CLI timeout après 30s avec un message clair.

### 8.4 Multiple watchers / sessions en parallèle

Le `PendingSessionsWatcher` traite les events séquentiellement (un `await
self._process_file()` à la fois) pour éviter les bizarreries avec les locks
des managers Claude / Codex. Si plusieurs drop-files arrivent en burst, ils
sont traités dans l'ordre de réception. Latence acceptable.

### 8.5 CLI qui crashe entre le drop et le polling

Le watcher traite quand même la demande et écrit le status. Les fichiers
(drop + status) restent sur disque jusqu'au prochain redémarrage du watcher,
qui détectera la co-présence drop+status et supprimera les deux. Pas de
fuite durable.

---

## 9. Codex et le canonical session_id

Côté Codex, le SDK mint son propre canonical_id différent du `request_uuid`.
Aujourd'hui, le WS consumer émet un broadcast `session_bound` pour que le front
réconcilie. La CLI n'écoute pas la WS, donc il faut que le canonical_id soit
remonté **dans le status file**.

Le service `create_session_from_payload()` retourne un `SessionCreationResult`
qui contient `session_id` = canonical_id (récupéré du manager après création).
Le watcher l'écrit dans le status. La CLI affiche le canonical_id, pas le
`request_uuid`. Côté Claude Code, les deux sont identiques de toute façon.

---

## 10. Messages d'erreur (wording)

**Tout le wording de la CLI est en anglais (CLAUDE.md project rule).** Les
chaînes ci-dessous sont les versions finales tapées par l'utilisateur ;
aucune n'est traduite côté code.

| Situation | Wording (anglais, version finale) | Code JSON |
|-----------|-----------------------------------|-----------|
| Heartbeat absent | `TwiCC server does not appear to be running (or is still starting up). Run \`twicc\` in another terminal and wait until it is ready.` | `server_down` |
| Heartbeat stale | `TwiCC server is unresponsive (last heartbeat {age}s ago). Make sure it is still running.` | `server_down` |
| Provider inconnu | `Unknown provider {x}. Available: {list}.` | `validation_error` |
| Provider disabled | `Provider {x} is disabled. Enable it from the UI or settings.` | `validation_error` |
| Setting invalide | `--{flag}: invalid value "{x}" for {provider}. Expected: {list}.` | `validation_error` |
| Setting non supporté par provider | `--{flag} is not supported by {provider}. Supported fields: {list}.` | `validation_error` |
| Preset inconnu | `Preset "{name}" not found for {provider}. Available: {list}.` | `validation_error` |
| Project id introuvable | `--project: id "{x}" not found (tried also with leading '-').` | `validation_error` |
| Project dir invalide | `--project: directory "{path}" does not exist.` | `validation_error` |
| Attachment trop gros | `--attach {file}: size {x} MB exceeds 5 MB limit.` | `validation_error` |
| Attachment MIME refusé | `--attach {file}: type {mime} not supported by {provider}.` | `validation_error` |
| Prompt vide | `Prompt is empty.` | `validation_error` |
| Prompt file vide | `Prompt: file "{path}" is empty.` | `validation_error` |
| Status `rejected` (générique) | `Rejected by server:` + détails de `errors[]` | `rejected` |
| Status `failed` | `Unexpected server error: {error}` | `failed` |
| Timeout post-`received` | `Request was received but server did not respond within {timeout}s. Check server logs.` | `timeout` |
| Timeout sans `received` | `No confirmation from server after {timeout}s.` (rare given the heartbeat pre-check) | `timeout` |

---

## 11. Risques et limitations connus

### 11.1 Sessions zombies pending

Si la CLI plante après avoir dropé mais avant d'avoir polled, le watcher
traitera la demande et les fichiers (drop + status) resteront jusqu'au
prochain redémarrage du watcher (qui les supprimera). Pas critique : la
session est bien créée, juste l'utilisateur ne saura pas son ID. Solution :
relancer la CLI avec les mêmes args → nouvelle session, double création.
C'est acceptable pour la v1.

### 11.2 Dégradation silencieuse cross-field

L'utilisateur peut taper `--effort max --model opus-4.5` et obtenir
silencieusement une session avec `effort=high`. Comme côté UI aujourd'hui.
Documenté, statu quo, **pas** d'ajout côté CLI. Si besoin un jour : ajouter un
`--strict` ou un mode `enforce_agent_settings_consistency(strict=True)`.

### 11.3 Resize d'image absent

Les images > 1568px peuvent être facturées plus cher ou refusées par certains
modèles Claude. Documenté dans le `--help`. v2 envisagée si besoin.

### 11.4 Concurrence projet auto-créé

Si deux CLI parallèles passent `--project /same/new/path` simultanément, les
deux feront `get_or_create`. Django garantit l'atomicité, l'une des deux
créera, l'autre lira la même row. Pas de doublon. Le `broadcast project_added`
sera potentiellement émis deux fois en cas de race, mais c'est inoffensif côté
front (idempotent dans le store).

### 11.5 Erreur d'auto-création du Project depuis la CLI

La CLI fait `Project.objects.get_or_create()` directement (a accès à Django).
Si l'auto-create réussit en DB mais que le broadcast WS échoue (channel layer
not initialized, par exemple — peu probable vu que le serveur tourne), le
projet est en DB mais le front ne se rafraîchit pas tout de suite. Acceptable :
au prochain refresh du front, le projet apparaît. Pas un blocker.

### 11.6 Latence Codex

Le `thread_start()` du SDK Codex peut prendre 1-2s. La CLI peut attendre
jusqu'à 30s par défaut. Acceptable.

### 11.7 Settings invalides envoyés au SDK

Si l'utilisateur s'amuse à craquer la validation CLI et envoie un payload mal
formé directement dans `sessions-pending/`, le watcher appelle
`create_session_from_payload` qui passe le payload au SDK. Le SDK peut planter,
ce qui devient un `failed` côté status. Pas critique : pas un vecteur d'usage
légitime.

### 11.8 Race heartbeat / serveur qui s'arrête entre le check et le drop

La CLI vérifie le heartbeat avant le drop. Le serveur peut s'arrêter dans la
fenêtre [check, drop]. Le drop file reste sur disque. La CLI timeout après
30s avec le wording dédié "No confirmation from server after {timeout}s.".
Au prochain redémarrage du serveur, le watcher détecte un drop-file sans
status → la session est créée (avec retard). Si l'utilisateur a aussi
relancé manuellement entre-temps, il y aura deux sessions identiques —
acceptable pour la v1.

### 11.9 Windows : pas de réelle isolation FS

Cf. section 6.5. Documenté comme limitation. Le projet est principalement
utilisé sur Linux/macOS d'après les wheels distribués, mais Windows reste
supporté en best-effort.

### 11.10 Observability côté CLI

La CLI n'écrit pas de log dans un fichier (à la différence du serveur). En
cas de bug, le debug se fait via `--json` (sortie machine-parseable) + le
log côté serveur (`<data_dir>/logs/backend.log`). Si une CLI plante en
silence avant le drop (e.g., crash Python), l'utilisateur n'a aucune trace.
Acceptable pour v1, à éventuellement adresser avec un flag `--verbose` plus
tard.

---

## 12. Plan de migration (non détaillé ici)

Ce chantier sera découpé en plusieurs commits, dans cet ordre suggéré (le plan
détaillé suivra dans un autre document) :

1. Helpers Python `get_agent_settings_choices` et `get_attachment_support` +
   exposition au bootstrap + cleanup des constants JS dupliquées.
2. Service `create_session_from_payload` extrait depuis `_handle_send_message`,
   avec le WS consumer adapté.
3. Heartbeat task au boot du serveur.
4. `PendingSessionsWatcher` côté back + intégration au démarrage des
   orchestrators.
5. Squelette de la commande CLI `session create` avec validation minimale
   (provider, project, prompt).
6. Module attachments + intégration.
7. Module presets + intégration.
8. Polling + output formatting + codes de retour.

Chaque commit fait passer les tests existants et n'ajoute pas de tests
(conformément au cadrage projet "no tests").

---

## 13. Compatibilité

- Pas d'impact sur le front au-delà du nettoyage des constants dupliquées.
- Pas d'impact sur l'API HTTP / WS au-delà du refacto interne de
  `_handle_send_message` qui appelle désormais un service partagé. Aucun
  changement de schéma de message externe.
- Pas de migration DB.
- Pas de breaking change.

---

## 14. Points résolus pendant le brainstorming

| Question | Décision |
|----------|----------|
| Mode d'exécution CLI | Lib-consumer + drop-file (option finale après 4 itérations). |
| Auth | Aucune — filesystem permissions suffisent. |
| Port discovery | Pas nécessaire — pas de réseau. |
| Découverte data_dir | `paths.get_data_dir()` (existant : env var ou défaut). |
| Placement de la commande | Top-level `twicc create-session` (pas sous `session_app` à cause du conflit Typer avec le callback positional). |
| Sémantique `--project` | **Optionnel** (défaut = cwd). Heuristique `os.path.isdir(value)` : path (absolu OU relatif) → auto-create après `realpath`. Sinon → project_id canonique avec acceptation du tiret initial omis (`home-twidi-dev-foo` ≡ `-home-twidi-dev-foo`), erreur si introuvable. |
| Sémantique prompt positional | Path existant → fichier. Sinon → texte brut. |
| Politique cross-field | Statu quo (dégradation silencieuse côté back). |
| Validation back par le watcher | Aucune au-delà de ce que fait `_handle_send_message`. |
| Heartbeat | Oui, à la fois côté back (5s, démarrée **APRÈS** `migrate` — rien ne tourne avant `migrate`) et check pré-drop côté CLI (tolérance 15s). Wording d'erreur CLI mentionne "or is still starting up" pour couvrir le warm-up. |
| Détection runtime provider | Côté watcher uniquement. CLI vérifie juste `disabledProviders`. |
| Codex canonical_id | `create_session()` du manager modifié pour retourner le canonical_id ; remonté dans le `SessionCreationResult` puis dans le status file. |
| Drop-file orphelin au boot | `_scan_existing` après `awatch` démarré ; sémantique basée sur co-présence drop/status (pas de TTL) : drop seul → traitement normal, drop+status → cleanup des deux, status seul → cleanup. |
| Resize image v1 | Non. Documenté. |
| Lazy import Django | Oui, pattern existant repris. |
| `--thinking` / `--claude-in-chrome` | Tri-state (`None`/`True`/`False`) via `typer.Option(None, "--thinking/--no-thinking")`. |
| Branche / worktree | `feature/cli-session-create` dans `.worktrees/feature-cli-session-create`. |
