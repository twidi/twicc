# Codex title sync — design

Document préparatoire à l'implémentation. Worktree : `feature/multi-provider`.
Toutes les citations sont du type `path:line`.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- **Sync down Codex → TwiCC.** Récupérer le titre des sessions Codex créées hors de TwiCC (CLI direct) quand l'utilisateur les a explicitement renommées dans Codex. Le titre vit dans la state DB interne de Codex, exposé via le SDK (`Thread.name`). Aujourd'hui, TwiCC ne le lit jamais — les sessions externes apparaissent sans titre.
- **Flush du `pending_title` → Codex.** Faire fonctionner pour Codex le mécanisme existant qui persiste le titre choisi par l'utilisateur sur un draft (« nouvelle session avec titre suggéré accepté ») une fois que la session existe réellement côté provider. Le mécanisme est en place pour Claude Code (`providers/claude_code/agent/manager.py:485-492`) mais n'a pas de consommateur côté Codex.

Les deux problèmes convergent sur la même direction : *Codex state DB = source de vérité, `Session.title` TwiCC = miroir local*. On les traite dans la même feature.

### 0.2 Ce qu'on NE FAIT PAS

- **Pas de poll périodique** pour détecter en live les renames faits via le CLI Codex pendant que TwiCC tourne. Au prochain restart, le sync down les ramasse — acceptable.
- **Pas de flag DB** type `Session.title_user_set` ou `Session.title_source`. L'invariant retenu rend ce marqueur inutile. Pas de migration de schéma.
- **Pas de `protect_title` côté Codex.** Le titre Codex ne vit pas dans le JSONL, donc pas de risque de re-append stale par un acteur tiers (contrairement à Claude Code où `protect_title` se défend des écritures du CLI qui ne savent pas que le titre a changé). Voir le commentaire existant `providers/codex/titles.py:4-9` qui le justifie.
- **Pas de modification du serializer.** La surcharge `get_pending_title(session.id) or session.title` (`core/serializers.py:48-51`) est intentionnelle : elle évite un flash visuel entre le moment où le user accepte un titre suggéré sur un draft et celui où le flush vers le provider termine. Confirmé explicitement par l'utilisateur.
- **Pas de backfill rétroactif des divergences existantes.** Une session Codex avec `Session.title` TwiCC déjà non-vide et `Thread.name` Codex différent sera rattrapée au prochain run du sync down (compute initial après déploiement, puis events watcher). Pas de script de migration dédié.
- **Pas de bouton "Sync titles" UI.** Trigger automatique uniquement.

### 0.3 Invariant central

**La state DB Codex (exposée via `Thread.name` du SDK) est la source de vérité pour le titre d'une session Codex.** `Session.title` TwiCC est un miroir cache synchronisé dans les deux sens :

- **TwiCC → Codex** : sur `rename_session()` via l'UI (déjà câblé, `providers/codex/helpers.py:322-336`) et sur le flush du `pending_title` à la 1ʳᵉ turn d'une nouvelle session (à ajouter), on appelle `thread.set_name()`. La state DB Codex est mise à jour, et `Session.title` est aligné dans la même opération.
- **Codex → TwiCC** : aux moments où on relit (background compute au boot, watcher sur nouvelle session), si `Thread.name` est non-null **et** différent de `Session.title`, on écrase `Session.title`.

Corollaire assumé : si l'utilisateur renomme via le CLI Codex après un rename TwiCC, le titre CLI gagne au prochain sync down. C'est cohérent avec « la dernière intention de l'utilisateur l'emporte ».

---

## 1. État de l'art

### 1.1 Comment Codex stocke un titre

- `Thread.name: str | None` (`src/codex_app_server/generated/v2_all.py:8191-8193`) — *Optional user-facing thread title*. Reste `None` tant que personne n'a appelé `thread/name/set` (CLI ou TwiCC).
- Codex **n'auto-génère pas** de titre à la création d'un thread — le premier message vit dans `Thread.preview: str` (`v2_all.py:8197-8202`), un champ séparé. Conséquence : une session externe que l'utilisateur n'a jamais nommée restera sans titre dans TwiCC, exactement comme aujourd'hui.
- Lecture en bulk : `thread_list(use_state_db_only=True, limit, cursor, …)` paginé (`src/codex_app_server/api.py:177-203` synchrone, `:374-401` async ; paramètre `use_state_db_only` documenté à `generated/v2_all.py:7055-7061` : *"If true, return from the state DB without scanning JSONL rollouts to repair thread metadata"*). Le mode `useStateDbOnly=true` est crucial : il évite le scan des rollouts JSONL et reste rapide même sur des installations avec beaucoup de sessions.
- Lecture ciblée : `thread_read(thread_id, include_turns=False)` (`api.py:649-653`). Un seul appel SDK pour un thread donné.
- Écriture : `thread.set_name(name)` (`api.py:655-657`).

On n'accède **jamais** au SQLite Codex directement. Tout passe par le SDK. Si Codex change le format de sa state DB, ça reste transparent pour nous.

### 1.2 Comment TwiCC gère un titre Codex aujourd'hui

- Stockage TwiCC : `Session.title: CharField(max_length=250)` (`src/twicc/core/models.py:280`). Pas de marqueur d'origine ni de timestamp dédié.
- Rename via l'UI : `CodexHelpers.rename_session()` (`providers/codex/helpers.py:322-336`) → `async_to_sync(rename_thread_via_sdk)` → `rename_thread_via_sdk()` (`providers/codex/titles.py:22-45`) qui spawn une `AsyncCodex` éphémère, appelle `thread_resume(thread_id)` puis `thread.set_name(title)`. Pas de protect_title, pas d'écriture JSONL. Le commentaire `titles.py:4-9` documente le choix.
- Initial sync : `providers/codex/initial_sync.py` walk `~/.codex/sessions/`, lit la 1ʳᵉ ligne JSONL pour extraire `session_id` + `cwd` + parent éventuel via `extract_session_meta()`. **Aucune lecture de `Thread.name`**.
- Watcher : `CodexSessionsWatcher.parse_session_file()` (`providers/codex/sessions_watcher.py:56-90`) fait le même boulot mais en réaction aux events `watchfiles`. **Aucune lecture de `Thread.name`**.
- Background compute : lancé dans `providers/codex/orchestrator.py:279` via `start_background_compute_task()`. Il calcule `kind`/`display_level`/etc. pour les sessions dont `compute_version` est obsolète. **Ne touche pas au titre**.

### 1.3 Le mécanisme `pending_title` et son trou côté Codex

Module provider-agnostique : `src/twicc/pending_titles.py`. Dict en mémoire `{session_id: title}`, trois opérations `set_/get_/pop_`.

| Acteur | Fichier | Lignes | Rôle |
|---|---|---|---|
| Producteur | `src/twicc/asgi.py` | 786-788 | WS handler `new_session` — appelle `set_pending_title()` quel que soit le provider. |
| Lecteur (affichage) | `src/twicc/core/serializers.py` | 48-51 | Le serializer surcharge `Session.title` avec le pending si présent. Anti-flash visuel, intentionnel. |
| Consommateur Claude Code | `src/twicc/providers/claude_code/agent/manager.py` | 485-492 | Sur transition `ASSISTANT_TURN` : `get_pending_title` → `rename_session_in_jsonl()` → `pop_pending_title()` → `protect_title()`. |
| Consommateur Codex | — | — | **Absent.** Zéro occurrence de `pending_title` dans `src/twicc/providers/codex/**`. |

Conséquence aujourd'hui pour Codex : un utilisateur qui accepte un titre suggéré sur une nouvelle session voit le titre immédiatement (via le serializer), mais ce titre n'est jamais persisté — ni dans `Session.title` ni dans la state DB Codex. Au prochain restart de TwiCC, le dict en mémoire disparaît et le titre est perdu.

C'est un oubli au moment de l'introduction du provider Codex, pas un choix délibéré. Les specs antérieures sur le sujet (`docs/superpowers/specs/2026-04-13-simplify-title-rename-via-sdk.md:80` : *"`pop_pending_title()` | Needed by manager to clear pending after successful flush"*) le décrivent comme un besoin standard du flow draft → session réelle.

---

## 2. Solution

Trois changements, tous additifs. Pas de suppression, pas de migration de schéma.

### 2.1 Flush du `pending_title` à la 1ʳᵉ turn (côté Codex)

Lieu : `src/twicc/providers/codex/agent/manager.py`, dans le hook de state change qui gère déjà les autres actions sur transition (`_on_state_change` ou équivalent — à confirmer à l'implémentation).

Pattern à dupliquer depuis `providers/claude_code/agent/manager.py:481-495`, en remplaçant l'écriture JSONL par un appel SDK :

```python
if state == AgentState.ASSISTANT_TURN:
    from twicc.pending_titles import get_pending_title, pop_pending_title

    pending = get_pending_title(agent.session_id)
    if pending:
        try:
            # The Codex thread already exists at this point — set_name is safe.
            # `agent._thread` is the AsyncThread set at agent.py:109.
            await agent._thread.set_name(pending)
            pop_pending_title(agent.session_id)
            # Reflect immediately in Session.title (the watcher would catch it
            # eventually but no need to wait).
            await sync_to_async(
                Session.objects.filter(id=agent.session_id).update
            )(title=pending)
        except Exception as e:
            logger.error(
                "Codex pending title flush failed for %s: %s",
                agent.session_id, e,
            )
            # Leave the pending in the dict — will retry on next ASSISTANT_TURN
            # transition (rare in practice; lost on restart, acceptable).
```

Différences avec la version Claude Code :
- Pas de `rename_session_in_jsonl()` (le titre Codex n'est pas dans le JSONL).
- Pas de `protect_title()` (pas de risque de re-append stale par un acteur tiers, cf. §0.2).
- Update `Session.title` en plus, pour éviter d'attendre un cycle watcher inutile (la connaissance est locale, autant la matérialiser tout de suite).

À l'implémentation : `agent._thread` est privé (`providers/codex/agent/agent.py:109`). On a deux options propres : (a) ajouter un accesseur public sur l'agent, (b) ne pas réutiliser le thread du manager et passer par le helper standalone `rename_thread_via_sdk(session_id, pending)` de `titles.py:22` — qui spawn une `AsyncCodex` éphémère, mais évite de toucher à l'encapsulation et garde le code symétrique avec le rename UI. Préférence à fixer au plan ; le standalone est probablement le bon choix par défaut.

### 2.2 Sync down bulk au boot (background compute)

Lieu : `src/twicc/providers/codex/orchestrator.py`, juste avant ou après `start_background_compute_task()` à la ligne 279-281 (à voir).

Logique :

1. Spawn une `AsyncCodex` éphémère (mêmes patterns que `titles.py:38-42`).
2. Paginer `thread_list(use_state_db_only=True, limit=N, cursor=...)` jusqu'à épuisement, construire `{thread_id: name}` en mémoire (uniquement les entrées où `name` est non-null et non-vide).
3. Pour chaque entrée, comparer avec `Session.title` en DB. Si différent → update `Session.title`. Broadcast WS `session_updated` standard pour propager aux clients connectés.
4. Logger un résumé (`%d titles synced from Codex`).

Helper dédié dans `src/twicc/providers/codex/titles.py` :

```python
async def bulk_sync_titles_from_codex() -> int:
    """Read Thread.name from Codex state DB for every known thread and
    update Session.title when it differs. Returns the count of updates."""
    # ...spawn AsyncCodex, paginate thread_list(use_state_db_only=True),
    # bulk_update sessions, broadcast diffs.
```

Choix d'isolation : le sync down n'est **pas** mis dans le compute lui-même. Le compute calcule des metadata par session (kind, display_level, etc.). Le sync down est une opération transversale de réconciliation entre deux sources de données — il appartient à l'orchestrator (qui sait combiner sync initial + compute + watcher), pas au compute.

**Règle d'écrasement** : on n'écrase `Session.title` que si `Thread.name` est non-null et différent. Si `Thread.name` est `None`, on **ne touche pas** à `Session.title` — ça évite de vider un titre TwiCC que Codex n'aurait pas (cas pathologique d'un rename TwiCC dont le `set_name` aurait échoué historiquement, par exemple).

### 2.3 Sync down ciblé au watcher (nouvelle session)

Lieu : `src/twicc/providers/codex/sessions_watcher.py`, dans le flow qui transforme un `ParsedSessionFile` en `Session` ORM. À ce stade je vois `parse_session_file()` qui retourne le `ParsedSessionFile`, puis le base watcher (`twicc/providers/sessions_watcher.py`) qui le matérialise — il faudra trouver le bon hook pour distinguer création vs update, et n'appeler `thread_read()` que sur création.

Logique :

1. Quand le watcher détecte qu'une **nouvelle** session apparaît (i.e. on s'apprête à créer une `Session` qui n'existait pas), faire `thread_read(session_id)` (un appel SDK ciblé).
2. Si `Thread.name` non-null et non-vide → utiliser comme `title` initial dans la création.
3. Si erreur SDK → log et créer la `Session` sans titre (comportement actuel, ne pas bloquer).

Pour les events watcher sur fichier déjà connu (modify), **pas de fetch**. C'est l'invariant "pas de poll" — si l'utilisateur rename via le CLI Codex après que la session est connue de TwiCC, on capte le rename au prochain restart via 2.2.

Helper dédié dans `src/twicc/providers/codex/titles.py` :

```python
async def read_title_from_codex(session_id: str) -> str | None:
    """Read Thread.name from Codex state DB for a single thread.
    Returns None if the thread has no name or on error (logged)."""
    # ...spawn AsyncCodex, await codex.thread_read(session_id, include_turns=False)
```

### 2.4 (Optionnel — à l'implémentation) Factoriser le spawn `AsyncCodex`

`titles.py:38-42` spawn une `AsyncCodex` éphémère pour `set_name`. 2.2 et 2.3 vont en spawn une chacune. Si la séquence devient verbeuse, on peut introduire un petit context manager `_codex_session()` interne au module. Pas un objectif en soi, juste une note pour la phase plan.

---

## 3. Edge cases

### 3.1 Race entre 2.1 (flush pending) et 2.3 (watcher sync down)

Les deux peuvent toucher la même session quasi simultanément quand un user accepte un titre suggéré sur un draft et envoie son premier message :

- **Cas A** : watcher voit la session avant que 2.1 ait flushé. `thread_read` retourne `name=None`. La règle 2.3 ne touche pas `Session.title` (cf. règle d'écrasement). Puis 2.1 flushe → `set_name` Codex + update `Session.title=pending`. État final : cohérent.
- **Cas B** : 2.1 flushe avant 2.3. `thread_read` retourne `name=pending`. La règle 2.3 écrit `Session.title=pending` (no-op si 2.1 l'avait déjà fait). État final : cohérent.

Pas de lock nécessaire — l'invariant "non-null wins, null no-op" rend l'ordre indifférent.

### 3.2 Race entre 2.2 (bulk boot) et le watcher

Le watcher démarre après le compute (cf. `providers/codex/orchestrator.py:285-288` — `search_index_ready` gate). Le bulk sync (2.2) est appelé autour du compute, donc **avant** que le watcher accepte des nouvelles sessions. Pas de race entre les deux dans le scénario normal.

Si une session apparaît pendant le bulk (cas rare : Codex CLI démarre en parallèle d'un boot TwiCC), elle peut soit être incluse dans la pagination du bulk soit être ramassée par le watcher au moment où il démarre — dans les deux cas elle reçoit son titre.

### 3.3 Rename via CLI Codex après rename via TwiCC

Cf. §0.3 corollaire. Le rename CLI gagne au prochain sync down. Comportement choisi (dernière intention user).

### 3.4 Sessions Codex avec `Session.title` non-vide et `Thread.name=None`

Cas pathologique : un rename TwiCC dont le `set_name` aurait échoué silencieusement à l'époque. La règle 2.2 / 2.3 ne touche pas — `Session.title` est préservé. Pas un cas régulier mais on l'accepte sans bruit.

### 3.5 Erreur SDK pendant un fetch

- 2.2 (bulk) : log, continue avec ce qui a été lu, et tente à nouveau au prochain restart. Pas de retry agressif (le SDK lui-même peut ne pas être prêt).
- 2.3 (single) : log, crée la `Session` sans titre. Pas de retry.
- 2.1 (flush) : log, garde le pending dans le dict. Re-essayé à la prochaine transition `ASSISTANT_TURN` si elle a lieu (peu probable). Perdu au restart — acceptable, c'est un cas dégradé du cas dégradé.

### 3.6 Broadcast WS sur changement de titre

Quand 2.2 ou 2.3 modifient `Session.title`, on déclenche un broadcast standard pour que les clients connectés voient le titre se matérialiser. Le canal exact (probablement le même que celui utilisé par `rename_session` actuel) sera précisé au plan d'implémentation.

---

## 4. Découpage logique (pour le plan)

À ne pas considérer comme un plan détaillé — juste une décomposition pour orienter la phase suivante.

1. Helper async `read_title_from_codex(session_id)` dans `providers/codex/titles.py`.
2. Helper async `bulk_sync_titles_from_codex()` dans `providers/codex/titles.py`.
3. Appel de (2) dans `providers/codex/orchestrator.py` autour du `start_background_compute_task`. Broadcast WS sur diff.
4. Hook dans le watcher (`providers/codex/sessions_watcher.py` ou le base watcher) pour appeler (1) à la création d'une `Session` Codex. Broadcast WS sur titre récupéré.
5. Flush du `pending_title` sur transition `ASSISTANT_TURN` dans `providers/codex/agent/manager.py`. Pattern Claude Code à `claude_code/agent/manager.py:481-495`.
6. Vérification manuelle : log à `INFO` du compte de titres synchronisés au boot ; test manuel "rename via CLI codex + restart TwiCC → titre apparaît dans l'UI" ; test manuel "draft TwiCC avec titre suggéré accepté → après 1ᵉʳ message, redémarrer → titre toujours là".

---

## 5. Hors scope explicite (récap)

- Pas de poll périodique en live pour les renames CLI Codex.
- Pas de migration de schéma DB.
- Pas de flag `title_user_set` / `title_source` / timestamp.
- Pas de `protect_title` côté Codex.
- Pas de modification du serializer (anti-flash intentionnel).
- Pas de bouton UI "Sync titles".
- Pas de réparation rétroactive des divergences pré-feature au-delà du sync down automatique.

---

## 6. Questions ouvertes

Aucune au moment de la rédaction — toutes les décisions sont tranchées dans les sections ci-dessus. Si quelque chose émerge en phase plan ou implémentation, on revient ici.
