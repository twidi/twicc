# Hidden sessions — design

**Date :** 2026-05-30
**Statut :** Draft
**Scope :** Backend (model + services + CLI + WS + FTS + stats) + Skills + Frontend (mineur)
**Worktree :** `feature/hidden-sessions` (dans `.worktrees/feature-hidden-sessions`)

Document de cadrage pour une feature donnant aux agents la possibilité de créer
des sessions **invisibles à l'utilisateur** — l'équivalent d'un sous-traitant
interne — tout en gardant la traçabilité de qui a spawné quoi via un champ
indépendant `spawned_by`.

---

## 0. Cadrage

### 0.1 Le besoin

Les commandes CLI et skills récents (`twicc create-session`, `twicc
send-message`, `twicc update-session`, `twicc process stop`, `twicc session`,
etc.) donnent à un agent une autonomie complète pour orchestrer des
sous-tâches : il peut spawn de nouvelles sessions (n'importe quel provider,
n'importe quel projet), envoyer des messages, attendre des réponses, modifier
les settings, attacher des fichiers, et stopper proprement.

Tant que ces sessions filles sont des sessions "normales", elles polluent
l'interface utilisateur : elles apparaissent dans la sidebar, dans les
recherches, dans les compteurs ("ce projet a 47 sessions"), dans les
statistiques d'activité — alors que ce sont des sous-tâches internes que
l'utilisateur n'a pas envie de voir.

On veut un mode "invisible" pour ces sessions : elles existent en DB,
consomment de l'API (coûts comptabilisés normalement, c'est de l'argent
réel), mais sont occultées de toute surface utilisateur. Conceptuellement,
c'est l'équivalent des `Session` de `type=SUBAGENT` (les subagents SDK
intra-process), avec deux différences :

- C'est une session **top-level** (`type=SESSION`), avec son propre provider,
  ses propres settings, son propre PID, ses propres JSONL, sa propre boucle
  agent
- Elle est créée explicitement via la CLI depuis un agent autonome
  (orchestrateur)

On veut **également** garder une trace : qui a créé qui ? Sans imposer que
cela soit lié à la visibilité. Une session visible peut très bien avoir été
spawnée par un agent (et c'est utile de le savoir). Et une session hidden
peut très bien être "racine" (créée par script humain, sans parente). Les
deux concepts sont orthogonaux.

### 0.2 Ce qu'on fait dans ce chantier

- **Nouveau champ `Session.hidden`** (boolean, mutable, default `False`)
- **Nouveau champ `Session.spawned_by`** (FK self, immuable, nullable) avec
  `related_name="spawned_sessions"` et `on_delete=SET_NULL`
- **Nouvelle commande CLI `twicc whoami`** + skill `twicc-whoami` : retourne
  les détails de la session courante (déduite par PID ancestry)
- **Auto-détection du `spawned_by`** dans `twicc create-session` via whoami,
  silencieuse, sans argument CLI exposé
- **Flag `--hidden`** sur `twicc create-session` avec contraintes de
  validation (`permission_mode` whitelist + `question_widget` forcé)
- **Nouvelles sous-commandes** `twicc update-session <ID> hide` / `unhide`
- **Filtrage par défaut des hidden** dans toutes les surfaces utilisateur
  (REST, WS, frontend, listings CLI, FTS)
- **Opt-in CLI** sur les listings : `--include-hidden`, `--only-hidden`,
  `--spawned-by <ID>`, `--spawned-by self`
- **Schéma Tantivy révisé** : ajout des champs `hidden` et `spawned_by` (ré-
  indexation via bump de `CURRENT_SEARCH_VERSION`)
- **Exclusion des hidden de tous les compteurs de sessions** (Project,
  Activity, frontend dérivés)
- **Inclusion des hidden dans tous les agrégats de coûts** (Project, Activity)
- **Nouveau type d'évènement WS** `session_removed` : émis lors d'un `hide`
  pour retirer la session des clients connectés
- **Mise à jour des SKILL.md** concernés : `twicc-create-session`,
  `twicc-update-session`, `twicc-sessions`, `twicc-processes`, `twicc-search`
- **Nouveau skill `twicc-whoami`**

### 0.3 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas de propagation automatique du flag `hidden` aux subagents SDK**
  (`type=SUBAGENT`) : ils sont déjà invisibles via le filtre `type=SESSION`
  appliqué partout. Aucune duplication de logique.
- **Pas d'env var `TWICC_SESSION_ID`** : remplacée par whoami (PID ancestry).
  Cf. §12.1 pour le rationale.
- **Pas d'argument `--spawned-by` sur `twicc create-session`** : la filière
  est résolue automatiquement et silencieusement par whoami. Cf. §12.2.
- **Pas de flag `--no-spawned-by`** : pas d'échappatoire pour casser
  intentionnellement la filière. Cf. §12.3.
- **Pas de mutation de `spawned_by` après création** : champ immuable
  (immuabilité par convention dans le service, pas une contrainte DB stricte).
- **Pas d'API REST pour exposer les hidden au frontend** : filtrage dur côté
  serveur, pas d'opt-in HTTP.
- **Pas d'UI utilisateur** pour créer / consulter / dé-hider une session : la
  feature est entièrement pilotée par la CLI/agent. Le user ne sait
  littéralement pas qu'elles existent (sauf via le coût agrégé du projet).
- **Pas de TTL / GC automatique** des sessions hidden : elles restent
  indéfiniment, comme n'importe quelle session.
- **Pas de filtre `hidden` ajouté au compute background** : les hidden sont
  computed normalement pour que les costs soient calculés.
- **Pas de validation cross-field supplémentaire** (la pré-validation est
  uniquement `permission_mode` + `question_widget`). Tout le reste passe par
  la chaîne de validation existante.
- **Pas de support pour le delete/recreate** de docs Tantivy lors du flip :
  une simple `reindex_session` met à jour le champ `hidden` du document.

### 0.4 Vocabulaire

| Terme | Définition |
|---|---|
| **Hidden session** | Session avec `hidden=True`. Invisible dans toutes les listes / affichages utilisateur, mais ses coûts continuent à être comptabilisés dans les agrégats. |
| **Spawned-by** | Champ `Session.spawned_by` (FK self, `related_name="spawned_sessions"`). Trace la session qui a invoqué la CLI pour créer cette session. **Indépendant** du flag `hidden`. |
| **Whoami (CLI)** | Commande `twicc whoami` qui retourne les détails de la session courante (déduite par PID ancestry). Échoue si aucune session n'est trouvée dans la chaîne PID. |
| **Auto-détection** | Mécanisme par lequel `twicc create-session` remplit silencieusement `spawned_by` via whoami. Aucun argument CLI exposé. |
| **Flip** | Opération de basculement du flag `hidden` (True ↔ False) via `twicc update-session <ID> hide` ou `unhide`. Déclenche un recompute synchrone des compteurs et une mise à jour du document FTS. |
| **PID ancestry** | Chaîne `current_pid → ppid → ppid.ppid → … → 1`. Whoami remonte cette chaîne et matche contre `ProcessRun.agent_pid`. |
| **"Filière"** | La relation `spawned_by`. Une session A peut spawner B qui spawne C : la filière C → B → A est traçable. |

---

## 1. Architecture globale

```
┌──────────────────────────────────────────────────────────────────────────┐
│  AGENT (Claude Code / Codex) — session_id S1                             │
│                                                                          │
│  Bash tool : "twicc create-session --hidden ..."                         │
│       │                                                                  │
│       │ subprocess (PID Pn)                                              │
│       ▼                                                                  │
│  ┌────────────────────────────────────────────────────────┐              │
│  │  twicc CLI                                             │              │
│  │                                                        │              │
│  │  1. whoami() → walk PID Pn → P_parent → … → P_agent    │              │
│  │     match ProcessRun.agent_pid → session_id S1         │              │
│  │  2. validate(--hidden + permission_mode + qw)          │              │
│  │  3. write drop-file with hidden=True, spawned_by=S1    │              │
│  └────────────────────────────────────────────────────────┘              │
│                                                                          │
│                      ┌─ TwiCC backend (uvicorn) ─┐                       │
│                      │                           │                       │
│                      │  pending_sessions_watcher │                       │
│                      │      ↓                    │                       │
│                      │  session_creation.py      │                       │
│                      │      ↓                    │                       │
│                      │  Session.objects.create(  │                       │
│                      │      hidden=True,         │                       │
│                      │      spawned_by=S1,       │                       │
│                      │      …)                   │                       │
│                      │      ↓                    │                       │
│                      │  AgentManager.start(S2)   │                       │
│                      └───────────────────────────┘                       │
│                                                                          │
│  Session S2 démarrée. Items écrits en JSONL → watcher → DB → broadcasts  │
│  mais filtrés par `if session.hidden: skip` à l'émission.                │
└──────────────────────────────────────────────────────────────────────────┘
```

Les composants modifiés :

| Composant | Modifications |
|---|---|
| Model `Session` | +2 champs (`hidden`, `spawned_by`), migration Django |
| CLI `create-session` | Flag `--hidden`, validations, auto-détection silencieuse de `spawned_by` |
| CLI `update-session` | Nouvelles sous-commandes `hide` / `unhide` |
| CLI nouveau `whoami` | Lookup PID ancestry, retourne détails session |
| CLI `sessions` / `processes` / `search` | Flags `--include-hidden`, `--only-hidden`, `--spawned-by <ID>`, `--spawned-by self` |
| `_session_request/validation.py` | Pré-validation hidden + permission_mode + question_widget |
| `_session_request/drop_file.py` | Payload transporte `hidden` et `spawned_by` |
| `core/services/session_creation.py` | Honore les nouveaux champs, refait la validation |
| `core/services/session_visibility.py` (NOUVEAU) | Orchestrateur du flip `hide`/`unhide` |
| `providers/sessions_watcher.py` | Guards `if session.hidden` sur broadcasts |
| `providers/db_writer.py` | Filtre `hidden=False` dans `recalc_sessions_count` |
| `core/models.py` (`PeriodicActivity`) | Filtres `hidden=False` sur compteurs (pas sur coûts) |
| `projects.py` | Filtre `hidden=False` dans `update_project_metadata` |
| `search.py` + `search_indexing_task.py` | Schéma Tantivy +2 champs, bump `CURRENT_SEARCH_VERSION`, filtres par défaut, flip → `reindex_session` |
| `asgi.py` | Guards sur broadcasts session_*, filtre `active_processes` au connect |
| `views.py` | Filtres `hidden=False` sur tous les endpoints session-liste |
| `core/serializers.py` | Expose `hidden` (utile à la CLI, ignoré frontend) |
| Frontend `store/data.js` | Defensive `if (session.hidden) continue` dans les unread counters ; handler nouveau type WS `session_removed` |
| Skills `twicc-*` | Mise à jour des SKILL.md + nouveau `twicc-whoami` |

---

## 2. Modèle de données

### 2.1 Nouveaux champs sur `Session`

Dans `src/twicc/core/models.py`, classe `Session` :

```python
hidden = models.BooleanField(default=False, db_index=True)
spawned_by = models.ForeignKey(
    "self",
    null=True,
    blank=True,
    default=None,
    on_delete=models.SET_NULL,
    related_name="spawned_sessions",
    db_index=True,
)
```

**Choix de design** :

- `hidden` : `BooleanField` simple, `db_index=True` car filtré dans la quasi-
  totalité des requêtes session-liste, default `False` (compat ascendante).
- `spawned_by` : FK self, `null=True` car la majorité des sessions n'ont pas
  d'agent parent (créées par l'utilisateur), `related_name="spawned_sessions"`
  pour pouvoir accéder à `session.spawned_sessions.all()`.
- `on_delete=SET_NULL` : si jamais une session parente est supprimée (pas de
  mécanisme de delete actuellement, mais futur-proof), les filles gardent
  `spawned_by=NULL`. Aucune cascade automatique.
- L'attribut DB-level est `spawned_by_id` (généré par Django) — utile pour
  l'accès direct sans hit DB et pour l'écriture (`spawned_by_id=X`).

### 2.2 Migration Django

Migration `add_field × 2`. Aucune data migration nécessaire :

- `hidden` : défaut `False` appliqué automatiquement à toutes les rows
  existantes (sémantiquement : sessions visibles, comportement préservé)
- `spawned_by` : défaut `NULL`, sémantiquement "sans parent CLI", correct
  pour toutes les sessions historiques

### 2.3 Index existant `idx_session_visible`

L'index `idx_session_visible` existe déjà sur `Session.Meta.indexes` (partial
index). À vérifier au moment de l'implémentation : si sa condition inclut
`archived=False`, on l'étend probablement avec `hidden=False` pour que la
majorité des requêtes de listing utilisent l'index. Si l'extension casse la
sémantique de l'index existant, on en ajoute un nouveau `idx_session_visible_v2`
sans toucher au premier. Décision finale au moment du plan d'implémentation,
après lecture des `Meta.indexes` actuels.

### 2.4 Pas dans `AgentSettings`

Les deux nouveaux champs ne sont **pas** ajoutés au tuple `AgentSettings`. Ce
sont des propriétés structurelles de la session, pas des paramètres réglables
de l'agent qui peuvent venir d'un preset. Ils ne sont pas non plus inclus
dans les `enforce_agent_settings_consistency` ou `apply_preset_overrides`.

### 2.5 Exposition dans `serialize_session`

`serialize_session` (`core/serializers.py:29`) **expose** les deux champs :
- `hidden: bool`
- `spawned_by: str | None` (UUID de la session parente, sérialisé comme string)

**Pourquoi exposer alors que le frontend ne reçoit jamais de hidden sessions ?**
Parce que la CLI utilise le même serializer pour ses commandes (`twicc
session`, `twicc whoami`, `twicc sessions --include-hidden`, etc.), et les
agents ont besoin de savoir si une session est `hidden` et qui l'a spawnée.

Côté frontend, comme les hidden ne passent pas le filtre REST, le champ
`hidden` est en pratique toujours `False` quand le frontend le voit. Aucune
exposition de surface UI nécessaire.

---

## 3. Mécanisme whoami et auto-détection

### 3.1 Algorithme whoami

Localisation : nouveau helper `src/twicc/cli/_session_request/whoami.py` :

```
def resolve_current_session() -> Session | None:
    # 1. Fetch DB once
    runs = ProcessRun.objects.exclude(state=DEAD).values("agent_pid", "session_id")
    pid_to_session_id = {r["agent_pid"]: r["session_id"] for r in runs if r["agent_pid"]}

    if not pid_to_session_id:
        return None

    # 2. Walk local PID ancestry
    pid = os.getpid()
    while pid > 1:
        pid = get_ppid(pid)  # cross-platform (psutil or /proc/<pid>/status fallback)
        if pid is None:
            return None
        if pid in pid_to_session_id:
            return Session.objects.get(pk=pid_to_session_id[pid])
    return None
```

**Propriétés** :

- **Une seule requête DB** pour récupérer tous les `ProcessRun` actifs.
  Lookup ensuite in-memory. Évite N requêtes pour N niveaux d'ancêtres.
- **Le premier match dans la chaîne gagne** : si l'agent A spawn la session
  B, et que B (en cours) spawn une session C, whoami depuis le contexte de C
  trouve B en premier (PID `agent_pid` de B est plus proche du PID courant
  que celui de A). C'est ce qu'on veut : la session "directement parente".
- **Si aucun match** : la fonction retourne `None`. C'est attendu pour un
  utilisateur humain qui invoque `twicc` depuis son terminal — il n'y a
  aucun `ProcessRun.agent_pid` dans sa chaîne.

**Stratégie cross-platform pour `get_ppid`** :

- Si `psutil` est dans les deps (à vérifier dans `pyproject.toml`) : usage
  préféré (`psutil.Process(pid).ppid()`).
- Sinon, fallback : `/proc/<pid>/status` (Linux) + `ps -o ppid= -p <pid>`
  (macOS / BSD). Cas Windows : non supporté (TwiCC est Linux/macOS only de
  fait).

### 3.2 Commande CLI `twicc whoami`

Nouveau fichier `src/twicc/cli/whoami.py`. Signature :

```
twicc whoami [--json]
```

Comportement :

- Appelle `resolve_current_session()`
- Si trouve une session → affiche les mêmes détails que `twicc session <ID>`
  (tous les champs sérialisés par `serialize_session`)
- Si rien trouvé → exit code non-zero (`1`) avec message clair :
  `"No TwiCC session found in PID ancestry. whoami is only meaningful from inside an active agent session."`
- Avec `--json` : sortie JSON équivalente, `{"error": "..."}` en cas
  d'absence (avec exit code 1 quand même).

### 3.3 Skill `twicc-whoami`

Nouveau skill court (`src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md`)
qui explique :

- Ce que la commande retourne (les mêmes détails qu'une session normale)
- Le cas d'usage : un agent qui veut connaître sa propre identité TwiCC, ses
  settings, ses coûts cumulés, son `spawned_by` (le cas échéant)
- L'échec gracieux quand pas dans une session

### 3.4 Auto-détection silencieuse dans `create-session`

`twicc create-session` n'expose **aucun** argument `--spawned-by`. Comportement
interne :

1. Avant la construction du payload, la CLI appelle `resolve_current_session()`
2. Si une session est trouvée → le payload est enrichi avec
   `spawned_by_session_id=<id>`
3. Si rien n'est trouvé → `spawned_by_session_id` est omis (devient `NULL`
   côté DB)
4. **Aucun log, aucun output, aucune option** : c'est transparent pour
   l'agent et pour l'utilisateur

**Conséquences voulues** :

- Un agent qui spawn une session : `spawned_by` est automatiquement renseigné,
  l'agent n'a rien à faire et ne sait pas que ce mécanisme existe.
- Un humain qui invoque `twicc create-session` depuis son terminal :
  `spawned_by` reste NULL, comportement actuel inchangé.
- Aucun moyen de "mentir" sur la filière (pas d'override CLI).
- Aucun moyen de "casser" la filière intentionnellement (pas de
  `--no-spawned-by`). Si un agent veut une session racine non liée, il devra
  invoquer la CLI depuis un sous-processus qui sort de la chaîne PID — pas
  un cas d'usage prévu.

### 3.5 Filtrage `--spawned-by self` sur les listings

Sur `twicc sessions`, `twicc processes`, `twicc search` (cf. §6.3), la valeur
spéciale `self` pour `--spawned-by` déclenche un appel whoami :

- Si whoami trouve une session → filtre = `spawned_by=<that_id>`
- Si whoami échoue → la commande échoue (exit non-zero) avec un message
  similaire à `twicc whoami`

`--spawned-by self` permet à l'agent de lister ses propres sessions filles
sans avoir besoin de connaître son propre ID.

---

## 4. Création d'une session hidden

### 4.1 Argument CLI `--hidden`

Sur `twicc create-session`, ajout d'un nouveau flag :

```
twicc create-session "<prompt>" --hidden [autres options…]
```

Sémantique : si présent, la session sera créée avec `hidden=True`. Absent :
`hidden=False` (default).

### 4.2 Contrainte permission_mode

Les sessions hidden ne doivent jamais provoquer une UI de demande
d'approbation, puisqu'aucun utilisateur ne va voir la session. La CLI
valide donc, **après résolution complète du `permission_mode` final**
(preset + overrides CLI + `enforce_agent_settings_consistency`) :

| Provider | Modes acceptés |
|---|---|
| Claude Code | `bypassPermissions`, `dontAsk` |
| Codex | `yolo`, `strict` |

**Sinon erreur explicite** mentionnant le provider et la liste autorisée :

```
Error: --hidden requires a non-interactive permission_mode.
Provider claude_code accepts: bypassPermissions, dontAsk
Got: default
```

Si le mode interdit vient d'un preset, la CLI le signale :

```
Error: --hidden requires a non-interactive permission_mode.
Preset 'default' sets permission_mode=default which is incompatible.
Provider claude_code accepts: bypassPermissions, dontAsk
Pass --permission-mode bypassPermissions to override.
```

### 4.3 Contrainte question_widget

Le `question_widget` est une feature Claude Code (UI de question contextuelle
dans la sidebar). Aucun sens pour une session hidden : on l'**oblige à
`False`**.

**Comportement choisi : erreur explicite si conflit** (cf. discussion brainstorming).

- Si le user passe `--no-question-widget` (ou que le préset met `False`) :
  OK, pas de conflit.
- Si le user passe `--question-widget` (`True`) explicitement, ou qu'un
  preset le met à `True` : erreur :

```
Error: --hidden is incompatible with question_widget=True.
Preset 'foo' enables question_widget which conflicts with --hidden.
Pass --no-question-widget to override.
```

Pour les providers qui n'utilisent pas `question_widget` (Codex), aucune
validation supplémentaire — le champ est ignoré.

### 4.4 Validation côté CLI et côté serveur (défense en profondeur)

**Côté CLI** (`_session_request/validation.py`, après `validate_settings`) :

- Nouvelle fonction `validate_hidden_constraints(provider, settings, hidden, bootstrap)`
- Appelée par **trois points d'entrée** :
  - `create-session` (validation à la création avec `--hidden`)
  - `update-session settings` (cf. §5.7) — si la session cible a `hidden=True`,
    refuser tout changement qui briserait l'invariant : `permission_mode` qui
    sort de la whitelist, ou `question_widget=True`. La sub-command doit
    fetcher la session avant de valider pour connaître son état `hidden`.
  - `update-session hide` (cf. §5.3) — vérifie que l'état courant satisfait
    déjà les contraintes (puisque la sub-command ne change que le flag).

**Côté serveur** (`core/services/session_creation.py` et le service
`session_visibility`) :

- Refont les mêmes validations à partir du payload / de la session reçue,
  avant `Session.objects.create()` ou `session.save()`.
- Si une validation échoue : le drop-file pour ce request reçoit un status
  d'erreur, la CLI sort en non-zero avec le message.

**Pourquoi double validation** : la CLI valide avec le bootstrap local
(rapide, pas de round-trip serveur). Le serveur revalide parce que le
drop-file est une frontière de confiance — un futur appelant (script
mal-formé, version désynchronisée) pourrait envoyer un payload invalide.

### 4.5 Propagation du payload

Le drop-file (`_session_request/drop_file.py`) transporte deux nouveaux
champs :

- `hidden: bool` (default `False` si absent)
- `spawned_by_session_id: str | None` (default `None` si absent)

Le service `create_session_from_payload` (`session_creation.py:161-178`) les
lit avec `payload.get("hidden", False)` et `payload.get("spawned_by_session_id")`,
les valide, puis les passe à `Session.objects.create(hidden=..., spawned_by_id=...)`.

---

## 5. Mutation du flag : `hide` / `unhide`

### 5.1 Sous-commandes

Sur le modèle des sous-commandes existantes `archive` / `unarchive` et `pin` /
`unpin` :

```
twicc update-session <SESSION_ID> hide
twicc update-session <SESSION_ID> unhide
```

Localisation : nouveau fichier `src/twicc/cli/update_session/hidden_command.py`
sur le pattern de `archived_command.py`.

### 5.2 Service backend

Nouveau service `src/twicc/core/services/session_visibility.py` exposant :

```
def hide_session(session: Session) -> None
def unhide_session(session: Session) -> None
```

Appelé depuis le drop-file watcher pour les updates (`kind="update_session"`,
sous-action `hide` / `unhide`). Implémente la logique synchrone du flip.

### 5.3 Pré-validations de `hide` (False → True)

Avant de toucher à la DB, le service vérifie :

1. **La session existe** et `type=SESSION` (pas un subagent — n'a pas de sens).
2. **`permission_mode` actuel** est dans la whitelist (`bypassPermissions` /
   `dontAsk` pour Claude, `yolo` / `strict` pour Codex). Sinon erreur :

```
Error: cannot hide a session with permission_mode=default.
Change it first with `twicc update-session <ID> settings --permission-mode bypassPermissions`.
```

3. **`question_widget`** est `False`. Sinon erreur similaire.
4. **Déjà hidden** : si `hidden=True` déjà, no-op avec message
   `"already hidden"`.

### 5.4 Pré-validations de `unhide` (True → False)

1. La session existe et `type=SESSION`.
2. Si `hidden=False` déjà : no-op avec message `"not hidden"`.

(Pas de contrainte sur `permission_mode` ni `question_widget` : la session
redevient visible, l'utilisateur peut configurer comme il veut.)

### 5.5 Mécanique du flip (synchrone)

Le service exécute, dans cet ordre :

1. **Toggle du flag** : `session.hidden = new_value` + `session.save(update_fields=["hidden"])`
2. **Recalcule `Project.sessions_count`** via `update_project_metadata(project)`
   et `db_writer.recalc_sessions_count(project)` (les deux paths qui écrivent
   ce champ).
3. **Recalcule les `PeriodicActivity`** : récupère les dates impactées (via
   `SessionItem.objects.filter(session=session).aggregate(min=Min("timestamp"), max=Max("timestamp"))`)
   et appelle `PeriodicActivity.recalculate_for_days(dates, project, provider)`
   pour `DailyActivity` et `WeeklyActivity`.
4. **Met à jour le document FTS** : `search.reindex_session(session_id)`. La
   ré-indexation ré-écrit le doc Tantivy avec le nouveau `hidden`. Pas de
   suppression / recréation explicite.
5. **Broadcasts WS** :
   - Toujours : `project_updated` (les compteurs ont changé)
   - Si `hide` (False → True) : `session_removed: {id: <session_id>}`
   - Si `unhide` (True → False) : `session_updated: {session: <serialized>}`
     (réintégration côté UI, comme une création tardive)

### 5.6 Performance

- `Project` : 2 queries en COUNT, négligeables
- `PeriodicActivity` : pour une session avec N jours d'items, N × 2 (Daily +
  Weekly) × 2 (count + sum) ≈ 4N queries. Pour 180 jours c'est ~720 queries,
  exécution en ~quelques centaines de ms.
- `reindex_session` : potentiellement coûteux pour une session avec
  beaucoup d'items (réécriture de tous les docs Tantivy). Mais c'est le même
  coût qu'un rename de titre actuel. Acceptable.

**Pas d'asynchrone** : pas de queue, pas de background task pour le flip.
Tout synchrone. Cohérent avec les autres opérations `archive` / `pin` qui
sont synchrones.

### 5.7 Concurrence

Le flip ne tente pas de "lock" la session. Si l'agent est en cours
d'exécution (en train d'écrire des items), le flip s'applique en concurrence :

- Les nouveaux items s'insèrent normalement (compute du cost ok)
- Les broadcasts `session_items_added` / `session_updated` filtrent à
  l'émission via `if session.hidden`

Race condition possible : entre le save du flag et le premier
`recalculate_for_days`, un nouvel item arrive et `PeriodicActivity` est
re-recalculé par le watcher. Le recalcul du watcher ré-applique correctement
le filtre `hidden=False`. Pas de divergence persistante.

### 5.8 Interaction avec `update-session settings`

`update-session settings --permission-mode <X>` peut être appelée sur une
session déjà hidden. Si `<X>` n'est pas dans la whitelist (`bypassPermissions`,
`dontAsk` pour Claude ; `yolo`, `strict` pour Codex) **et** que la session
est actuellement hidden → la commande doit refuser avec une erreur claire
(« cannot set permission_mode=default on a hidden session; unhide first
with `twicc update-session <ID> unhide` »).

Idem pour `update-session settings --question-widget` sur une session hidden
(Claude Code) : refus avec message similaire.

Implémentation : le handler de `settings` fetch la session, et si
`session.hidden=True`, branche `validate_hidden_constraints` après le merge
preset+overrides+enforce_consistency, avant d'écrire.

Si la session est `hidden=False`, aucune validation supplémentaire — le
comportement actuel de `settings` est préservé.

---

## 6. Filtrage et visibilité

Principe : **invisible nulle part par défaut, accessible par opt-in explicite
dans la CLI uniquement**.

### 6.1 REST endpoints

Le filtre est dur côté API. Aucun paramètre HTTP pour inclure les hidden (le
frontend n'en a aucun usage légitime).

| Endpoint | Fichier:ligne | Modification |
|---|---|---|
| `GET /api/sessions/` | `views.py:81` (`_get_sessions_page`) | `.filter(hidden=False)` |
| `GET /api/projects/<id>/sessions/` | même helper | idem |
| `GET /api/sessions/<id>/` | `views.py:461` (`_resolve_session_or_404`) | 404 si `hidden=True` |
| `GET /api/projects/<id>/sessions/<id>/` | idem | idem |
| `POST /api/sessions/bulk-archive/` | `views.py:721` | Ajouter `hidden=False` au filter (les hidden ne sont jamais bulk-archivées) |
| `GET /api/search/` | `views.py:2060` | Filtre Tantivy `hidden=False` (cf. §8) + enrichment ORM `.filter(hidden=False)` en defense in depth |
| `GET /api/home/` | `views.py:1912` | Pass-through via `WeeklyActivity` aggregates (automatique une fois la base fixée, cf. §7) |
| `GET /api/daily-activity/` et `/api/projects/<id>/daily-activity/` | `views.py:1977` | Idem pass-through |

### 6.2 WebSocket broadcasts

Les broadcasts session-liés sont filtrés à l'émission par
`if session.hidden: return` :

| Émetteur | Fichier:ligne | Modification |
|---|---|---|
| `session_updated` (file watcher, nouvelle session) | `providers/sessions_watcher.py:568` | guard `if session.hidden: return` |
| `session_updated` (file watcher, stale recovery) | `providers/sessions_watcher.py:692` | idem |
| `session_updated` (HTTP PATCH archive/pin/rename) | `views.py:601-612` | idem |
| `session_updated` (send_message settings update) | `asgi.py:779-788` | idem |
| `session_updated` (session_viewed) | `asgi.py:1543-1553` | idem |
| `session_updated` (mark_session_read_state) | `asgi.py:1601-1612` | idem |
| `session_items_added` | `providers/sessions_watcher.py:585-596` | idem |
| `sessions_bulk_archived` | `views.py:793-799` | Pas modifié (les hidden sont exclues du bulk via filtre amont) |
| **`session_removed`** (NOUVEAU type) | service `session_visibility.hide_session` | Émis lors du flip `hide` |
| `project_updated` | `db_writer.py:2147-2163`, `providers/sessions_watcher.py:509,610` | **NON filtré** — les coûts agrégés bougent y compris pour les hidden, le frontend doit le voir |
| `active_processes` (WS connect) | `asgi.py:327-415` | Filtrer : exclure les `ProcessRun` dont la session est hidden |

### 6.3 Listings CLI

Le CLI expose des opt-in explicites. Les flags `--include-hidden` et
`--only-hidden` sont **mutuellement exclusifs** (validation Typer).

| Commande | Default | Opt-in |
|---|---|---|
| `twicc sessions` | exclut hidden | `--include-hidden` (mélange), `--only-hidden` (exclusif), `--spawned-by <ID>` (filtre), `--spawned-by self` (whoami + filtre) |
| `twicc processes` | exclut hidden (filtre la jointure Session) | idem |
| `twicc search "<query>"` | exclut hidden (filtre Tantivy) | `--include-hidden`, `--only-hidden`, `--spawned-by <ID>`, `--spawned-by self` |
| `twicc session <ID>` | accepte tout ID, y compris hidden | n/a (lookup direct) |
| `twicc send-message <ID>` | accepte tout ID | n/a |
| `twicc update-session <ID>` | accepte tout ID | n/a |
| `twicc process <ID>` / `process <ID> stop` | accepte tout ID | n/a |
| `twicc whoami` | retourne la session courante (qu'elle soit hidden ou non) | n/a |

**Conséquences** :

- Pas besoin de "connaître" un ID pour interagir : les lookups directs
  acceptent les hidden.
- Mais pour les listings, il faut opt-in explicite — c'est cohérent avec
  "invisible par défaut".
- Note : `twicc processes` exclut totalement la row du ProcessRun (pas
  d'affichage avec `title=None`). Décision : "invisible nulle part" doit
  être total.

### 6.4 Frontend store

**Garantie principale** : la couche REST filtre les hidden côté serveur,
donc aucune hidden ne devrait jamais entrer dans le dict `state.sessions`
du store Pinia. Les compteurs et listes dérivés héritent automatiquement de
cette garantie.

**Defensive guards** : on ajoute néanmoins `if (session.hidden) continue` à
**tous** les getters qui itèrent `state.sessions` :

| Getter | Fichier:ligne | Raison |
|---|---|---|
| `getProjectUnreadCount` | `data.js:550-563` | Compteur unread, fuite ⇒ badge erroné |
| `getGlobalUnreadCount` | `data.js:621-634` | Compteur unread global |
| `getProjectSessions` | `data.js:446-476` | Liste sidebar par projet |
| `getAllSessions` | `data.js:477-500` | Vue "All projects" |
| `getNextMruPath` | `data.js:3166` | MRU navigation |

**Pourquoi ces 5 et pas d'autres** : ce sont les seuls getters identifiés
qui itèrent `state.sessions` (ou un dérivé direct). Les autres compteurs et
listes sont des pass-through de champs déjà filtrés côté backend
(`project.sessions_count`, `WeeklyActivity.session_count`, etc.) et ne
nécessitent rien. Liste à valider lors du plan en cherchant
`Object.values(state.sessions)` et patterns équivalents.

**Pas dans le scope frontend** : aucune UI utilisateur pour gérer les hidden
(switch, filtre, etc.). Le user n'a aucune surface pour interagir avec ces
sessions.

---

## 7. Compteurs et coûts

Le user a tranché explicitement : **coûts toujours comptés, sessions count
jamais comptées**.

### 7.1 Tableau exhaustif des compteurs et coûts

**Hidden exclus** (compteurs de sessions / messages) :

| Compteur | Fichier:ligne | Modification |
|---|---|---|
| `Project.sessions_count` (path 1) | `projects.py:323` (`update_project_metadata`) | `.filter(hidden=False)` |
| `Project.sessions_count` (path 2) | `db_writer.py:2387-2392` (`recalc_sessions_count`) | `.filter(hidden=False)` |
| `PeriodicActivity.session_count` | `core/models.py:187-194` (`recalculate`) | `.filter(hidden=False)` |
| `PeriodicActivity.user_message_count` | `core/models.py:168-175` | join `.filter(session__hidden=False)` |
| Search progress counter | `search_indexing_task.py:327` | `.filter(hidden=False)` |
| Orchestrator log counter (Claude) | `claude_code/orchestrator.py:463` | `.filter(hidden=False)` (justesse log) |
| Orchestrator log counter (Codex) | `codex/orchestrator.py:425` | idem |

**Hidden inclus** (coûts) :

| Champ | Fichier:ligne | Modification |
|---|---|---|
| `Project.total_cost` | `core/models.py:86` (`recalculate_total_cost`) | **inchangé** |
| `PeriodicActivity.cost` | `core/models.py:178` | **inchangé** |

### 7.2 Pass-through (correct automatiquement)

Les compteurs frontend dérivés sont des pass-through des compteurs backend :

| Composant | Fichier:ligne | Pass-through depuis |
|---|---|---|
| `HomeView` (`totalSessionsCount`) | `frontend/src/views/HomeView.vue:26-27` | `project.sessions_count` (déjà fixé) |
| `WorkspaceCard` (`totalSessionsCount`) | `frontend/src/components/workspace/WorkspaceCard.vue:56-57` | idem |
| `ProjectCard` | `frontend/src/components/project/ProjectCard.vue:113` | idem |
| `ProjectDetailHeader` | `frontend/src/components/project/ProjectDetailHeader.vue:122-124` | idem |
| `ActivityDashboard` (`sessionCount`, totals) | `frontend/src/components/.../ActivityDashboard.vue:45-54, 198-212` | `daily_activity` / `weekly_activity` API (déjà fixé) |
| `ContributionGraph` / `ContributionSparklines` | dérivés des mêmes API | idem |
| `SearchOverlay` (`totalSessions`) | `frontend/src/components/app/SearchOverlay.vue:120, 277, 310` | search API (cf. §8) |
| Backend vues `home_data` / `daily_activity` / `weekly_activity` | `views.py:1944, 2030, 2038` | aggregations `Sum("session_count")` (déjà fixé) |

### 7.3 Pas de compteur Workspace

Workspaces sont du JSON pur (pas de modèle Django). Pas de compteur DB. Le
`totalSessionsCount` du workspace est calculé côté frontend via somme des
`project.sessions_count` membres — automatique.

### 7.4 Recompute lors du flip

Cf. §5.5 et §5.6. Synchrone, peu coûteux pour les cas réels.

### 7.5 `UsageSnapshot` (quotas)

Pas impacté. Les snapshots viennent des APIs provider (Anthropic, …), pas des
sessions locales. Une session hidden consomme du quota provider de la même
manière qu'une session visible — c'est de la consommation réelle, comptée
côté provider. Aucune divergence locale possible.

---

## 8. Full-text search (Tantivy)

### 8.1 Schéma révisé

Le schéma Tantivy actuel possède (entre autres) un champ `archived: bool`.
On en ajoute deux :

- `hidden: bool`
- `spawned_by: str` (UUID ou empty string pour `NULL`)

Localisation : `src/twicc/search.py`, autour de `init_search_index`.

### 8.2 Mécanisme de ré-indexation

Le mécanisme existant `CURRENT_SEARCH_VERSION` dans
`search_indexing_task.py:312-316` re-indexe toutes les sessions dont la
`search_version` est antérieure à la version courante. **On bump cette
constante** pour cette feature.

Au prochain démarrage de TwiCC, le bulk indexer ré-indexe toutes les
sessions avec le nouveau schéma. Pas de downtime — le watcher continue à
indexer les nouvelles sessions normalement pendant ce temps.

Note implémentation : l'index Tantivy sur disque doit être détruit et
recréé (conflit de schéma si on ajoute des champs à un index existant). Le
mécanisme actuel gère probablement déjà ça en supprimant `search-index/` au
bump de version — à confirmer dans le plan d'implémentation.

### 8.3 Indexation universelle (incluant les hidden)

**Décision** : les hidden sont indexées dans Tantivy. Discussion brainstorming
(cf. §12.5) : sans indexation, un agent ne peut pas chercher dans ses propres
sessions filles, ce qui est un cas d'usage prime. Le coût (taille d'index,
re-indexation au flip) est marginal.

| Plug | Fichier:ligne | Modification |
|---|---|---|
| Startup bulk indexer | `search_indexing_task.py:312` | **Pas de filtre `hidden`** ; doc Tantivy porte les nouveaux champs |
| Live indexer (sessions_watcher) | `providers/sessions_watcher.py:402` (`_index_new_items_for_search`) | Pas de skip ; passe `hidden=session.hidden`, `spawned_by=session.spawned_by_id` |
| `reindex_session` | `search.py:267` | Pas d'early-return ; lit `session.hidden` et `session.spawned_by_id` |

### 8.4 Filtres par défaut

| Surface | Filtres par défaut |
|---|---|
| REST `GET /api/search/` (UI utilisateur) | `hidden=False` forcé côté Tantivy + filtre ORM post-search en defense in depth |
| CLI `twicc search "<q>"` | `hidden=False` par défaut, `--include-hidden` enlève le filtre, `--only-hidden` force `hidden=True`, `--spawned-by <ID>` ou `self` ajoute un filtre |

### 8.5 Flip et re-indexation du doc

Lors d'un `hide` ou `unhide` : `search.reindex_session(session_id)` met à
jour le champ `hidden` du document. Plus simple et plus symétrique que
delete + recreate. Confirmé : `reindex_session` couvre le cas où le doc
existait avant et le ré-écrit avec les nouveaux champs.

---

## 9. WebSocket : nouveau type `session_removed`

### 9.1 Payload

```json
{
  "type": "session_removed",
  "session_id": "<uuid>"
}
```

Émis depuis `session_visibility.hide_session` après le save du flag, **avant**
les broadcasts `project_updated`.

### 9.2 Frontend handler

Nouveau case dans le `useWebSocket.js` (ou équivalent) qui appelle un
nouveau getter / mutation du store data : `removeSession(sessionId)`.
Effet : suppression de la session du dict `state.sessions`, et tout ce qui
en dérive (sidebar, projet detail, etc.) se met à jour automatiquement via
les getters Pinia.

### 9.3 Pas réutilisation

Recherche dans la codebase frontend pour vérifier qu'il n'existe pas déjà
un type `session_deleted` ou `session_removed`. À confirmer au moment du
plan. Si un type équivalent existe, on le réutilise. Sinon, on crée
`session_removed`.

---

## 10. Skills et documentation CLI

### 10.1 Modifications des SKILL.md

| Skill | Modification |
|---|---|
| `twicc-create-session` | Documenter `--hidden` avec ses contraintes (permission_mode whitelist, question_widget forcé `False`). **Pas mentionner `spawned_by`** (auto, transparent). Ajouter section "Related commands" mentionnant `twicc sessions --spawned-by self` et `twicc search --spawned-by self` pour retrouver les sessions créées par cette session. |
| `twicc-update-session` | Ajouter sous-commandes `hide` / `unhide` avec leurs préconditions (permission_mode + question_widget compatibles avant `hide`). |
| `twicc-sessions` | Ajouter `--include-hidden`, `--only-hidden`, `--spawned-by <ID>`, `--spawned-by self`. |
| `twicc-processes` | Idem. |
| `twicc-search` | Idem. |
| `twicc-whoami` (NOUVEAU) | Court : explique que la commande retourne les détails de la session courante (auto-détectée via PID ancestry), et qu'elle échoue gracieusement si on n'est pas dans une session. |

### 10.2 Help strings CLI

Mettre à jour les `help=` strings de Typer pour tous les nouveaux args
(`--hidden`, `--include-hidden`, `--only-hidden`, `--spawned-by`). Cohérent
avec le ton des autres descriptions.

### 10.3 Pas de README global

Pas de nouveau document de top-level README. La feature est entièrement
documentée via les skills, les help strings CLI, et ce design doc.

---

## 11. Pièges et points de vigilance

Issus de la cartographie. À garder en tête pendant l'implémentation et la
review du plan.

1. **Background compute** (`background_compute_task.py`) : ne **pas** filtrer
   `hidden`. Les coûts doivent être computed normalement pour les hidden.

2. **Subagents (`type=SUBAGENT`)** : déjà invisibles partout via le filtre
   `type=SESSION`. Aucune propagation `hidden` à mettre. Confirmé : aucune
   session-liste / compteur ne fait référence aux subagents sans filtrer
   `type=SESSION`.

3. **Crons (`SessionCron`)** : ne créent pas de nouvelles sessions (elles
   envoient un message à la session porteuse). Si une cron pointe vers une
   session hidden, elle continue à fonctionner — ni interface ni CLI ne
   liste les crons d'une session par défaut. Si jamais on ajoute une UI
   "voir les crons d'une session", elle devra hériter du filtre `hidden`.
   À noter dans le plan.

4. **Index `idx_session_visible`** : à étendre avec `hidden=False` si
   pertinent. Décision finale en lisant le `Meta.indexes` au moment de
   l'implémentation.

5. **`pending_sessions_watcher`** : doit propager `hidden` et
   `spawned_by_session_id` du payload vers `session_creation.py`. Le service
   refait les validations (défense en profondeur).

6. **`serialize_session`** : expose `hidden` (utile à la CLI). Frontend
   ignore le champ (toujours `False` en pratique côté UI).

7. **`session_removed` event** : nouveau type WS, doit être implémenté côté
   frontend dans le handler générique (`useWebSocket.js` ou équivalent).

8. **Création + premier message d'une session hidden** : pas de race
   condition. Le watcher écrit les items normalement (cost computed), seuls
   les broadcasts sont skip.

9. **Flip `hide` pendant une session active** : la session continue à
   tourner, son cost s'accumule normalement. Le filtre `hidden` agit en aval
   (broadcasts, listings), pas sur le pipeline d'écriture.

10. **Données existantes** : `hidden=False` et `spawned_by=NULL` par défaut.
    Aucune migration de données nécessaire.

11. **`twicc processes` join** : décision = exclure totalement la row
    (« invisible nulle part »), pas afficher avec `title=None`.

12. **`spawned_by on_delete=SET_NULL`** : si jamais une session parente est
    supprimée (pas de mécanisme actuel mais futur-proof), les filles gardent
    `spawned_by=NULL`. Pas de cascade.

13. **Cycle prévention** : pas nécessaire. FK simple, pas de propagation
    calculée à la création. Un agent ne peut pas spawn lui-même via la CLI
    de toute façon (whoami retournerait son propre ID, et `Session.objects.create`
    avec `spawned_by=self` ne donnerait pas de cycle puisque le `self` est
    déjà-créé).

14. **Performance `reindex_session` sur grosse session lors d'un `unhide`** :
    à surveiller mais pas bloquant (cas rare). Cohérent avec le coût d'un
    rename existant.

15. **`CURRENT_SEARCH_VERSION` bump** : déclenchera une ré-indexation de
    TOUTES les sessions au prochain démarrage, pas seulement celles touchées
    par cette feature. Cohérent avec le mécanisme existant.

16. **Récursion de hidden** : un agent dans une session hidden peut créer
    une autre session hidden. Chaîne `spawned_by` correcte (auto-détectée).
    Aucune limitation à imposer.

17. **`AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`** : terme préexistant dans le
    code (concerne des fields d'`AgentSettings` non exposés au frontend, sans
    rapport avec notre `Session.hidden`). Pas de collision technique mais
    sémantique. À noter pour qu'un futur lecteur ne confonde pas.

18. **Provider activation** : ne pas oublier que `--hidden` doit fonctionner
    pour chaque provider supporté (Claude Code + Codex). Tests à inclure
    dans le plan d'implémentation.

19. **Le `twicc whoami` peut être appelé par un humain** : exit non-zero
    avec un message clair. Pas de stack trace, pas de crash. Pas de bruit
    excessif. Cohérent avec le ton des autres erreurs CLI.

20. **Bundle agent settings** (`CLAUDE.md → Frontend Patterns → Agent
    Settings`) : `hidden` n'est PAS dans le bundle (cf. §2.4). Les
    `getAgentSettingsCategories()` ne le mentionnent pas. Confirmer en
    relisant `frontend/src/providers/baseHelpers.js` pendant l'implémentation.

---

## 12. Approches considérées et écartées

### 12.1 Env var `TWICC_SESSION_ID`

**Idée écartée** : injecter `TWICC_SESSION_ID=<session_id>` dans l'env du
subprocess agent au moment du spawn. L'agent (et tous ses subprocess Bash)
hériteraient de l'env var et pourraient la lire.

**Raison de l'écart** :
- Claude Code : pré-allocation de l'ID possible → injection faisable.
- Codex : ID généré par le binaire après `thread_start` → injection après
  coup nécessaire, et le subprocess agent doit être déjà démarré.
- Surtout : whoami via PID ancestry est plus homogène (même mécanisme pour
  les deux providers), plus robuste (pas de pollution d'`os.environ`), et
  plus simple à raisonner (pas de cas particulier "subprocess pas encore
  démarré").

### 12.2 Argument `--spawned-by <ID>` sur `create-session` avec validation anti-spoofing

**Idée écartée** : exposer un argument explicite `--spawned-by`. Validation
contre whoami : si l'agent passe un ID différent du sien → erreur
"forbidden".

**Raison de l'écart** : le user a tranché pour la simplicité ultime — pas
d'argument du tout, auto-détection silencieuse. Pas de risque de spoofing,
pas de validation à écrire, skill `twicc-create-session` ne mentionne pas
le concept.

### 12.3 `--no-spawned-by` pour casser intentionnellement la filière

**Idée écartée** : permettre à un agent de créer une session "racine" non
liée à lui.

**Raison de l'écart** : pas de cas d'usage légitime identifié. Si jamais le
besoin émerge, on pourra l'ajouter sans casser la compat.

### 12.4 `hidden` dans le bundle `AgentSettings`

**Idée écartée** : ajouter `hidden` au tuple `AgentSettings` aux côtés des
sept paramètres existants (`selected_model`, `effort`, etc.).

**Raison de l'écart** : `hidden` est une propriété structurelle de la
session, pas un paramètre réglable de l'agent qui peut venir d'un preset.
La logique de validation (whitelist `permission_mode`) est différente. Le
flux de mutation (`hide` / `unhide`) est dédié, pas couvert par
`update-session settings`. Le mettre dans le bundle aurait ajouté des
exceptions partout dans la chaîne (validation, presets, frontend categories,
etc.).

### 12.5 Ne pas indexer les sessions hidden dans Tantivy

**Idée écartée** : épargner l'index Tantivy en sautant l'indexation des
hidden. Comportement initial du design.

**Raison de l'écart** : un agent qui orchestre des sessions hidden veut
pouvoir y chercher (`twicc search "trouve ce dont on a parlé tout à
l'heure" --spawned-by self`). Sans indexation, ce cas d'usage est cassé.
Coût marginal : 2 champs supplémentaires (boolean + UUID) par doc, et un
filtre par défaut `hidden=False` côté query pour ne pas leak vers l'UI.

### 12.6 Sub-command `twicc update-session <ID> settings --hidden`

**Idée écartée** : exposer le toggle `hidden` comme un agent setting via
`settings --hidden` / `settings --no-hidden`.

**Raison de l'écart** : `hidden` n'est pas un agent setting (cf. §12.4).
Pour la mutation, on préfère la cohérence avec les sous-commandes dédiées
existantes : `archive` / `unarchive`, `pin` / `unpin`, → `hide` / `unhide`.

### 12.7 Flag immutable après création

**Idée écartée** : `hidden` fixé à la création, non modifiable.

**Raison de l'écart** : utile de pouvoir "remonter" une session hidden
intéressante après-coup (rendre visible) ou "cacher" une session après
qu'on a décidé qu'elle est trop bruyante. Le coût de mutation (recompute
synchrone) est acceptable.

### 12.8 Filtrage dur des hidden côté REST search

**Idée maintenue** : aucun opt-in côté REST. Mais on s'est posé la question
d'un éventuel `?include_hidden=true` pour un usage admin/debug.

**Raison de la décision** : le frontend n'a aucune surface pour gérer un
mode "voir les hidden". Si jamais on en avait besoin un jour, on l'ajouterait.
Pour l'instant, la CLI est la seule porte d'entrée.

---

## 13. Cartographie des fichiers concernés

Compilation des fichiers à toucher pour le plan d'implémentation. Référence,
pas exhaustif (le plan d'implémentation détaillera).

### 13.1 Backend — Modèle et services

| Fichier | Modifications |
|---|---|
| `src/twicc/core/models.py` | +2 champs sur `Session` ; ajustements dans `PeriodicActivity.recalculate` ; éventuellement étendre `idx_session_visible` |
| `src/twicc/core/services/session_creation.py` | Honore `hidden` et `spawned_by_session_id`, refait validations |
| `src/twicc/core/services/session_visibility.py` (NEW) | Implémente `hide_session` / `unhide_session` |
| `src/twicc/core/serializers.py` | Expose `hidden` et `spawned_by` dans `serialize_session` |
| `src/twicc/projects.py` | `update_project_metadata` : filtre `hidden=False` sur `sessions_count` |
| `src/twicc/providers/db_writer.py` | `recalc_sessions_count` : filtre `hidden=False` |

### 13.2 Backend — CLI

| Fichier | Modifications |
|---|---|
| `src/twicc/cli/__init__.py` | Enregistrer la nouvelle commande `whoami` |
| `src/twicc/cli/whoami.py` (NEW) | Commande `twicc whoami` |
| `src/twicc/cli/_session_request/whoami.py` (NEW) | Helper `resolve_current_session()` |
| `src/twicc/cli/_session_request/validation.py` | `validate_hidden_constraints` |
| `src/twicc/cli/_session_request/drop_file.py` | Transporte `hidden` et `spawned_by_session_id` |
| `src/twicc/cli/create_session/command.py` | Flag `--hidden`, appel whoami pour auto-détection |
| `src/twicc/cli/update_session/__init__.py` | Enregistrer les nouvelles sous-commandes |
| `src/twicc/cli/update_session/hidden_command.py` (NEW) | Sous-commandes `hide` / `unhide` |
| `src/twicc/cli/sessions.py` | Flags `--include-hidden`, `--only-hidden`, `--spawned-by` |
| `src/twicc/cli/processes.py` | Idem |
| `src/twicc/cli/search.py` | Idem + transmission à `search.py` |

### 13.3 Backend — WS / broadcasts / FTS

| Fichier | Modifications |
|---|---|
| `src/twicc/providers/sessions_watcher.py` | Guards sur broadcasts ; passe `hidden` et `spawned_by_id` à `index_document` |
| `src/twicc/providers/claude_code/sessions_watcher.py` | Idem si broadcasts spécifiques Claude |
| `src/twicc/asgi.py` | Guards sur broadcasts ; filtre `active_processes` au connect |
| `src/twicc/views.py` | Filtres `hidden=False` sur les endpoints listés en §6.1 |
| `src/twicc/search.py` | Schéma Tantivy +2 champs ; `index_document` signature ; `search` filtres par défaut |
| `src/twicc/search_indexing_task.py` | Bump `CURRENT_SEARCH_VERSION` ; passe les nouveaux champs |
| `src/twicc/pending_sessions_watcher.py` | Propage `hidden` et `spawned_by_session_id` du payload |

### 13.4 Frontend

| Fichier | Modifications |
|---|---|
| `frontend/src/stores/data.js` | Defensive guards dans `getProjectUnreadCount` / `getGlobalUnreadCount` ; nouveau handler `session_removed` (suppression de la session du dict) |
| `frontend/src/composables/useWebSocket.js` (ou équivalent) | Routing du nouveau type `session_removed` |
| `frontend/src/providers/baseHelpers.js` | Vérifier que `hidden` n'apparaît pas dans les categories — silence souhaité |

### 13.5 Skills

| Fichier | Modifications |
|---|---|
| `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md` | Documenter `--hidden` + contraintes + section "Related commands" mentionnant `--spawned-by self` |
| `src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md` | Documenter `hide` / `unhide` |
| `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md` | Documenter les nouveaux flags |
| `src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md` | Idem |
| `src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md` | Idem |
| `src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md` (NEW) | Nouveau skill court |

### 13.6 Migration

| Fichier | Modifications |
|---|---|
| `src/twicc/core/migrations/00XX_session_hidden_and_spawned_by.py` (NEW) | `AddField × 2` |

### 13.7 Documentation projet

Pas de modification de `CLAUDE.md` ou `AGENTS.md` pour cette feature
spécifique. La feature est entièrement documentée via ce spec, les skills
et les help strings CLI. (Cf. memory `feedback_agents_md_sync_with_claude_md` :
la règle de sync s'applique aux modifications de top-level `CLAUDE.md`, ce
qui n'est pas le cas ici.)

---

## 14. Open questions

Aucune question bloquante. Quelques points secondaires à confirmer pendant
l'implémentation, sans bloquer la rédaction du plan :

1. **Index `idx_session_visible`** : sa définition exacte (à lire dans
   `Meta.indexes`) déterminera si on l'étend ou si on en ajoute un second.
   Décision technique mineure, n'affecte pas le contour de la feature.

2. **`psutil` dans les deps** : si déjà présent, on l'utilise pour `get_ppid`.
   Sinon, fallback `/proc` + `ps`. Pas de nouvelle dépendance à ajouter pour
   cette feature seule.

3. **`search-index/` rebuild** : confirmer que le bump de
   `CURRENT_SEARCH_VERSION` déclenche bien la suppression du dossier avant
   re-création (pour éviter les conflits de schéma Tantivy). Sinon, l'ajouter
   au plan.

4. **Validation côté serveur du `spawned_by_session_id` reçu** : doit-on
   vérifier que l'ID référence une session existante ? Probablement oui (FK
   contraint), mais on doit gracieux-fail si l'ID est invalide (drop-file
   forgé). Décision : `Session.objects.create(spawned_by_id=<invalid>)`
   lèvera une `IntegrityError` capturée par le service et renvoyée comme
   erreur claire dans le status file. À implémenter dans le plan.

(Note : la question "type WS `session_deleted` ou équivalent préexistant à
réutiliser" est traitée en §9.3.)
