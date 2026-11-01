# Provider Activation — design

**Date :** 2026-05-16
**Statut :** Draft
**Scope :** Backend + Frontend
**Worktree :** `feature/multi-provider`

Document de cadrage pour permettre à l'utilisateur d'activer / désactiver
individuellement les providers (`claude_code`, `codex`, et tout futur provider)
côté TwiCC. La machinerie d'un provider désactivé est totalement coupée
(orchestrator, watcher, periodic tasks, plugin Codex) ; les données déjà en DB
restent consultables.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Une clé `disabledProviders` dans `settings.json` qui pilote l'état actif /
  inactif de chaque provider.
- Au premier lancement (ou après upgrade depuis une version sans cette clé),
  l'utilisateur choisit lui-même les providers à activer via un dialogue
  bloquant.
- Dans le panneau Settings existant (section *Providers*), un switch par
  provider pour activer / désactiver à chaud.
- Côté back : démarrer uniquement les orchestrators des providers actifs au
  boot, et les start / shutdown à chaud quand l'utilisateur change un switch.
- Côté front : restreindre toute la mécanique runtime (création de session,
  rotation usage, indicateurs de statut, sections settings spécifiques, etc.)
  aux providers actifs ; remplacer le MessageInput par un callout quand une
  session ouverte appartient à un provider désactivé.
- Empêcher de désactiver le **dernier** provider actif et empêcher de
  désactiver un provider qui a une **session active** en cours.

### 0.2 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas de gestion d'échec** lors d'une activation à chaud. On part en mode
  **ultra-optimiste** : le switch bascule immédiatement, le back fait son
  travail en arrière-plan, on suppose que ça ne plante pas. Pas de loader,
  pas de rollback.
- **Pas de désinstallation** du plugin TwiCC dans `~/.codex/config.toml` quand
  Codex est désactivé. Le plugin reste installé ; il sera réutilisé tel quel à
  la réactivation, et invoqué via standalone `codex` quoi qu'il arrive (le CLI
  TwiCC est installé alongside).
- **Pas d'action particulière sur le search index.** Il continue d'indexer ce
  que les watchers lui poussent ; un provider désactivé n'a plus de watcher
  donc plus de nouvelles entrées indexées, ce qui est l'effet voulu. Aucune
  désindexation des sessions historiques.
- **Pas de filtrage en lecture** sur les sessions. La liste, les détails, les
  items, les recherches continuent de remonter les sessions de tous les
  providers présents en DB, qu'ils soient actifs ou non.
- **Pas de migration de données.** La présence ou l'absence de la clé
  `disabledProviders` dans `settings.json` suffit à distinguer les cas. Aucune
  copie / réécriture / valeur par défaut à initialiser hors du dialogue.

### 0.3 Vocabulaire

| Terme | Définition |
|-------|-----------|
| **Provider compilé** | Un provider présent dans `getRegisteredProviders()` côté front et dans le `ProviderHelpersRegistry` côté back. C'est ce que la version installée de TwiCC sait gérer. |
| **Provider actif** | Un provider compilé qui ne figure pas dans `disabledProviders`. Synonyme : *enabled*. |
| **Provider désactivé** | Un provider compilé qui figure dans `disabledProviders`. |
| **Session active** | Côté back : une session pour laquelle l'`AgentManager` du provider concerné maintient une instance d'agent (`ClaudeAgent` ou `CodexAgent`) vivante. Côté front : l'information est connue via le store qui tracke les agents en cours. |
| **Dialogue initial** | La modale non-fermable hébergée dans `App.vue` qui force l'utilisateur à choisir au moins un provider quand l'état n'est pas exploitable (clé absente OU tous désactivés). |

---

## 1. Modèle de configuration

### 1.1 Clé `disabledProviders`

Ajoutée aux `synced settings` (déjà existants, lus / écrits par
`src/twicc/synced_settings.py`).

```json
{
  "_version": 5,
  "defaultProvider": "claude_code",
  "disabledProviders": ["codex"],
  ...
}
```

- **Type :** liste de strings, chaque élément étant la valeur d'un membre de
  l'enum `Provider`.
- **Valeur par défaut :** liste vide. Ne pas la lister dans
  `_GENERIC_SYNCED_SETTINGS_DEFAULTS` — l'absence physique de la clé dans le
  fichier sert de sentinelle pour le dialogue initial (cf. §2). Le côté back
  doit donc lire « clé absente vs clé présente vide » de manière distincte
  (deux états sémantiquement différents).
- **Validation :** seuls les noms de providers compilés sont acceptés. Toute
  entrée inconnue est silencieusement ignorée à la lecture (sans réécrire le
  fichier). Tout doublon est dédupliqué à l'écriture.
- **Choix « liste négative » :** quand un futur 3ᵉ provider sera compilé, il
  sera automatiquement actif chez les utilisateurs existants (il ne figure
  pas dans `disabledProviders`). Pas de migration nécessaire.

### 1.2 Source dérivée `enabled_providers`

À calculer au vol des deux côtés :

```python
# back
def get_enabled_providers() -> set[Provider]:
    settings = read_synced_settings()
    disabled = set(settings.get("disabledProviders") or [])
    return {p for p in get_registered_providers() if p.value not in disabled}
```

```js
// front, exposé depuis le store des synced settings
export function getEnabledProviders() {
  const disabled = new Set(store.disabledProviders ?? [])
  return getRegisteredProviders().filter(p => !disabled.has(p))
}
```

Cette fonction est la source unique de vérité utilisée partout pour les
décisions runtime. `getRegisteredProviders()` n'est plus appelé directement
sur les chemins runtime (création de session, rotation usage, etc.) ; il reste
utilisé pour les besoins non-runtime (parsing de contenu DB, dialogue initial,
panneau Settings qui doit afficher *tous* les switches y compris ceux des
providers désactivés).

### 1.3 `defaultProvider` — validation croisée

- À la lecture : si `defaultProvider` n'est pas dans `enabled_providers`,
  retomber sur le premier élément de `enabled_providers` (ordre stable :
  ordre de définition de l'enum).
- À l'écriture (toggle d'un provider qui devient désactivé) : si le défaut
  pointait dessus, le back **bascule automatiquement** la valeur stockée vers
  un autre provider actif et broadcast ce changement avec le toggle.
- Côté front (Settings) : le `<wa-select>` du *default provider* n'affiche
  que les providers actifs.

---

## 2. Dialogue initial

### 2.1 Conditions d'affichage

Le front affiche le dialogue si :

1. La clé `disabledProviders` est **absente** des synced settings reçus, **OU**
2. La clé est présente mais `enabled_providers` est vide (tous les providers
   compilés sont dans la liste — typiquement édition manuelle / corruption /
   futur provider qu'on n'aurait pas voulu).

Sinon, pas de dialogue.

### 2.2 Pré-sélection — règle unique

Quel que soit le cas, la pré-sélection des cases s'obtient par dérivation
simple :

> *« Pré-cocher chaque provider compilé qui n'est pas dans
> `disabledProviders` (valeur lue, ou liste vide si la clé est absente). »*

- Cas 1 (clé absente) → liste vide → **tout est pré-coché**.
- Cas 2 (tous désactivés) → liste = providers compilés → **rien n'est
  pré-coché**.
- Tout cas intermédiaire (édition manuelle qui ne désactive qu'une partie) →
  pré-coché conforme à la valeur du fichier.

Pas de logique conditionnelle distincte par cas : une seule formule qui marche
partout.

### 2.3 Comportement de la modale

- Hébergée dans `App.vue`, donc affichée quelle que soit la route active.
- **Non fermable** : pas de croix, pas d'Esc, pas de clic outside, pas de
  bouton « Annuler ». Seul un bouton *Save* permet de la quitter.
- *Save* est **désactivé** tant qu'aucun provider n'est coché.
- Au clic sur *Save* : envoyer la nouvelle valeur `disabledProviders` au back
  via le canal de sync settings (cf. §5). Le front attend la confirmation par
  réception du synced settings broadcast, puis ferme la modale.
- Texte explicatif court dans la modale : *« TwiCC supporte plusieurs
  providers. Sélectionnez ceux que vous souhaitez activer. Vous pourrez
  changer ce choix à tout moment dans Settings → Providers. »*

### 2.4 Position dans le cycle de boot

- À l'init de l'app Vue : si la condition d'affichage est satisfaite **avant
  même que `start_all()` n'ait démarré quoi que ce soit** côté back (le back
  ne start rien tant que l'état est ambigu — cf. §3), on affiche la modale et
  on bloque toute interaction.
- Conséquence : on n'a pas besoin de gérer un état « providers en cours de
  démarrage » dans le front à ce stade. Quand la modale se ferme, le back a
  reçu la nouvelle config, lancera les orchestrators, et le front recevra
  ensuite les données normalement.

---

## 3. Boot du back

### 3.1 `start_all()` conditionnel

Dans `OrchestratorRegistry.start_all(...)` :

- Lire `disabledProviders` (ou son absence) au démarrage.
- **Si la clé est absente OU si `enabled_providers` est vide** : ne démarrer
  aucun orchestrator. Le serveur HTTP / WS / settings continue de tourner ;
  c'est l'attente du choix utilisateur.
- **Sinon** : itérer sur les providers compilés, ne `start()` que ceux qui
  sont dans `enabled_providers`.

Les tâches cross-provider du CLI (prix OpenRouter, version check, search
index) ne sont pas concernées par cette gate — elles tournent toujours, peu
importe quels providers sont actifs.

### 3.2 Validation initiale via le dialogue

Quand le front envoie la nouvelle valeur `disabledProviders` au back (sortie
du dialogue initial) :

- Le handler de sync settings la persiste (mécanisme existant).
- Il déclenche ensuite, **dans la foulée**, le démarrage des orchestrators
  des providers nouvellement actifs (réutilise la logique de toggle à chaud
  décrite en §4.3).

Pas de redémarrage du serveur nécessaire.

### 3.3 État « ambigu » côté API

Tant qu'on est dans la branche « ne démarrer aucun orchestrator », les
endpoints runtime sont tous censés répondre par `ProviderDisabledError` (cf.
§6). Le front, lui, n'envoie aucun appel runtime parce qu'il est bloqué sur
la modale — mais la défense en profondeur reste.

---

## 4. Toggle à chaud

### 4.1 UX — ultra-optimiste

Décision : on assume que le back ne plante pas à l'activation / désactivation.

- **Clic sur un switch dans Settings → Providers → bascule immédiate de
  l'état visuel.** Pas de mode loading, pas d'animation d'attente, pas de
  rollback en cas d'échec.
- Le front envoie la nouvelle `disabledProviders` au back via sync settings.
- Le back applique (cf. §4.3 / §4.4), broadcast le résultat.
- Le front est censé voir ses callouts disparaître / apparaître quand le
  broadcast arrive (cf. §5).

Si à terme on veut gérer les échecs, on rajoutera un loader / rollback.
Pas dans ce chantier.

### 4.2 Désactivation — garde-fou sessions actives

Le seul cas qu'on refuse vraiment est : **désactiver un provider qui a des
sessions actives en cours**.

**Côté front (UX préventive) :**

- Le switch est **grisé** (disabled) dès qu'on sait qu'il existe au moins une
  session active pour ce provider (info déjà présente dans le store front qui
  tracke les agents en cours).
- Message d'aide sous le switch : *« Impossible de désactiver : sessions
  actives en cours. »*

**Côté back (défense en profondeur, race condition possible) :**

- Au moment d'appliquer une nouvelle `disabledProviders`, le back inspecte
  son `AgentManager` pour chaque provider concerné.
- Si un provider qu'on tente de désactiver a au moins un agent vivant : le
  back **annule** cette désactivation dans son traitement, retire ce provider
  de la `disabledProviders` reçue, persiste la valeur corrigée, et broadcast
  cette valeur corrigée comme synced settings (avec son `_version` incrémenté
  normalement).
- Effet : tous les devices connectés reçoivent l'état corrigé, le switch
  revient à *ON* automatiquement. L'utilisateur peut être surpris une demi-
  seconde — c'est acceptable étant donné qu'on a déjà mis le garde-fou UI.
- Aucun message d'erreur envoyé spécifiquement ; le simple fait que le
  broadcast revienne avec une valeur différente de celle envoyée suffit (le
  cas reste exceptionnel — race condition pure).

**Pas de garde-fou « dernier provider »** côté back : c'est déjà bloqué
côté front (cf. §7.5). Si quelqu'un édite manuellement le fichier pour tout
désactiver, on retombe sur le **dialogue initial** (cf. §2) qui force la
résolution.

### 4.3 Activation à chaud

Quand `disabledProviders` perd un provider qui y figurait :

- Le back appelle `BaseOrchestrator.start()` du provider concerné. Cet appel
  est le même que celui du boot normal, incluant tout son setup :
  - Initial sync de ses sessions (catch-up de tout ce qui a été écrit dans
    les JSONL pendant la période off).
  - Lancement du watcher.
  - Lancement du background compute si le `CURRENT_COMPUTE_VERSION` du
    provider a bougé pendant la période off (logique existante, non
    spécifique à ce chantier).
  - Lancement des periodic tasks (usage, statuspage, model_retirement, …).
  - Pour Codex : `ensure_twicc_plugin_installed()` (idempotent ; ne fait
    rien si déjà installé).

### 4.4 Désactivation effective

Quand `disabledProviders` gagne un provider (passe le garde-fou §4.2) :

- Le back appelle `BaseOrchestrator.shutdown()` du provider concerné. Cet
  appel est le même que celui d'un shutdown normal :
  - Arrêt du watcher.
  - Annulation des periodic tasks.
  - Cleanup éventuel (déjà géré provider par provider).
- Si le provider qu'on désactive était le `defaultProvider` : le back fait
  basculer `defaultProvider` vers un autre provider actif (premier de
  `enabled_providers` dans l'ordre de l'enum) **dans la même opération de
  sync settings**, de façon atomique. Un seul broadcast.

---

## 5. Propagation au front

### 5.1 Pas de canal dédié

`disabledProviders` est une clé de synced settings. Elle hérite donc
automatiquement de tout le mécanisme existant :

- **Bootstrap initial** : exposée via `/api/bootstrap/` (snapshot des synced
  settings déjà inclus).
- **Reconnexion WebSocket** : re-broadcast du snapshot synced settings au
  hello.
- **Changement live** : `synced_settings_updated` broadcast par le back vers
  tous les devices connectés quand un device modifie la valeur.

Aucun nouveau type de message WS à créer.

### 5.2 Réaction côté front

Le store synced settings reçoit la nouvelle valeur, `getEnabledProviders()`
recalcule. Tous les composants qui en dépendent réagissent par réactivité
Vue :

- Settings → section Providers : les switches reflètent le nouvel état.
- Section settings spécifique d'un provider : se masque / se démasque.
- MessageInput / draft : le sélecteur de provider se met à jour.
- Vue session ouverte d'un provider qui vient d'être désactivé : le
  callout (cf. §7.2) apparaît.
- Rotation usage : la liste interne `usageProviders` recalcule, le provider
  désactivé sort de la rotation.
- Indicateurs Anthropic / OpenAI dans le footer Settings : disparaissent dès
  que la source `_statusAwareProviders` filtre sur enabled.

Note : les détails exacts de hooks / watchers à ajouter dans les composants
existants sont laissés au plan ou à l'implémentation (cf. §8).

---

## 6. Garde-fou back

### 6.1 API

Nouveau module léger (un seul fichier, p. ex.
`src/twicc/providers/enabled.py` — nom définitif à fixer au plan ou à l'implémentation) :

```python
class ProviderDisabledError(Exception):
    def __init__(self, provider: Provider):
        self.provider = provider
        super().__init__(f"Provider {provider.value} is disabled")

def is_provider_enabled(provider: Provider) -> bool: ...
def ensure_provider_enabled(provider: Provider) -> None:
    if not is_provider_enabled(provider):
        raise ProviderDisabledError(provider)
```

L'implémentation lit `disabledProviders` depuis la même source de cache que
les synced settings (déjà en mémoire après lecture initiale). Pas d'appel
disque à chaque check.

### 6.2 Application — check **explicite**

`ensure_provider_enabled(provider)` est appelé **au début** de chaque
endpoint / handler WS qui pilote du runtime de provider :

- Création de session.
- Resume / continue / interrupt d'un agent.
- Envoi de message vers un agent.
- Toute action mutante qui touche un agent vivant (changement live d'agent
  settings, etc.).
- Tout autre handler WS qui s'adresse à un provider runtime.

La liste exhaustive sera dressée au moment de l'implémentation (cf. §8).

### 6.3 Pas d'application en lecture seule

Les endpoints / handlers de lecture (`GET /api/sessions/...`,
`GET /api/projects/...`, fetch d'items, etc.) **ne sont pas** gatés. Les
données restent consultables côté front.

### 6.4 Mapping erreur

- HTTP : `ProviderDisabledError` est mappée vers un `409 Conflict` avec
  `{"error": "provider_disabled", "provider": "<value>"}`.
- WebSocket : message d'erreur de même forme, type d'event à définir à
  l'implémentation (suit la convention existante des erreurs WS).

### 6.5 Registries

- `ProviderHelpersRegistry` : **inchangé**. Les helpers servent au parsing
  read-only de sessions historiques, accessibles même provider désactivé.
- `OrchestratorRegistry` et `AgentManagerRegistry` : on peut s'autoriser à
  retourner `None` (ou une instance « stopped ») pour les providers
  désactivés, pour faciliter les call-sites qui voudraient lazy-check. À
  trancher au plan ou à l'implémentation. Le contrat externe reste : pas d'appel
  runtime sans `ensure_provider_enabled`.

---

## 7. Comportements front quand un provider est désactivé

### 7.1 Aucun filtrage des sessions affichées

La liste de sessions, le détail d'une session, la recherche, les workspaces,
le sidebar : tout continue de remonter et d'afficher les sessions de tous les
providers présents en DB. Pas de filtre `enabled_providers` ici.

### 7.2 Callout dans une session ouverte d'un provider désactivé

Dans `SessionItemsList.vue` (composant qui héberge déjà le `stale-banner`
inline pour les sessions stale) :

- Ajouter une condition complémentaire qui rend un callout *similaire au
  stale-banner* (réutiliser le style existant si possible, ou variante
  dédiée) quand `!isEnabled(session.provider)` et qu'on n'est pas dans un
  sous-agent contexte.
- Le callout remplace le MessageInput, message du genre : *« This provider
  is currently disabled. Re-enable it in Settings → Providers to resume this
  session. »*
- Forme finale (composant dédié ou variante du stale-banner) : à fixer à
  l'implémentation (cf. §8).

### 7.3 Actions sur une session de provider désactivé

| Action | Comportement |
|--------|-------------|
| Envoi d'un nouveau message (reprise d'une session) | **Désactivé** : le callout remplace le MessageInput. |
| Rename | **Désactivé** : l'opération appelle le SDK du provider (`rename_session` côté Claude Code, `thread/name/set` côté Codex via `rename_thread_via_sdk`). L'endpoint `views.py:rename` doit refuser avec `ProviderDisabledError` quand le provider est off ; côté UI, l'action est masquée / grisée. |
| Archive / unarchive | Reste disponible (purement applicatif, DB TwiCC uniquement). |
| Pin / unpin | Reste disponible (purement applicatif, DB TwiCC uniquement). |

Note : les autres actions évoquées dans les discussions (retry, branch, delete, export) n'existent pas dans TwiCC à ce jour. Rien à gérer pour elles ; si elles sont ajoutées plus tard, ce design devra être complété pour préciser leur comportement.

### 7.4 MessageInput / draft / agent settings popup

À la création d'une nouvelle session (état draft) :

- Le **sélecteur de provider** (qu'on peut ouvrir aujourd'hui depuis l'agent
  settings popup) liste uniquement les providers actifs.
- Si **un seul** provider est actif, **le sélecteur n'est pas affiché du
  tout**. L'agent settings popup garde ses autres options ; juste pas de
  picker provider.
- Pas de risque de se retrouver à 0 provider à choisir : le dialogue initial
  empêche cet état (cf. §2), et le bouton dans Settings empêche aussi
  l'utilisateur de désactiver le dernier provider (cf. §7.5).

### 7.5 Panneau Settings — section *Providers*

- En tête de la section, **avant** la liste des sections de settings
  spécifiques : un bloc « *Activated providers* » avec un switch par
  provider compilé.
- Sous chaque switch :
  - Si le provider a des sessions actives : message d'aide « *Cannot disable:
    active sessions in progress.* » et switch grisé.
  - Si le provider est le seul actif : message d'aide « *Cannot disable:
    at least one provider must remain active.* » et switch grisé.
- Texte global au-dessus de la liste : court paragraphe rappelant que
  désactiver un provider empêche de créer / resume des sessions et coupe
  toutes ses tâches en arrière-plan.
- Sections de settings spécifiques (`provider_<key>`) :
  - **Cachées** dans la sidebar quand le provider est désactivé.
  - Les **valeurs stockées sont conservées** côté back (pas de reset, pas de
    purge). À la réactivation, le panneau réapparaît avec ses valeurs.

### 7.6 Default provider — sélecteur dans Settings

Le `<wa-select>` existant pour `defaultProvider` :

- N'affiche que les providers actifs.
- Si la valeur stockée pointe sur un provider désactivé (cas transitoire ou
  édition manuelle), le back l'a déjà corrigée (cf. §1.3 / §4.4) — le front
  reçoit donc toujours une valeur cohérente. Pas de logique défensive
  particulière à ajouter côté front.

### 7.7 Indicateurs Anthropic / OpenAI (footer Settings)

Pas de changement structurel : la liste `_statusAwareProviders` itère sur
`getEnabledProviders()` (au lieu de `getRegisteredProviders()`). Les
indicateurs des providers désactivés disparaissent automatiquement parce que
leur `statuspage_task` ne tourne plus côté back et qu'on les exclut de la
rotation côté front.

### 7.8 Rotation usage (ProjectView)

`usageProviders` filtre sur `getEnabledProviders()` (au lieu de
`getRegisteredProviders()`). Le provider désactivé sort de la rotation.

---

## 8. Détails fixés plus tard (plan ou implémentation)

Liste explicite des points laissés ouverts à ce stade, à trancher soit pendant
l'écriture du plan d'implémentation, soit au moment du code, selon le cas :

1. **Définition opérationnelle exacte de « session active »** côté back (et
   alignement avec le store front qui fournit l'info au switch). Probablement
   « agent instancié dans `AgentManager` », mais à valider en regardant
   l'état actuel du store front et le code de `AgentManager`.
2. **Liste exhaustive des endpoints HTTP et handlers WS** qui appellent
   `ensure_provider_enabled`. À dresser en grep-ant les call sites runtime
   actuels.
3. **Forme finale du callout « provider disabled »** dans la session :
   réutilisation directe du composant stale ou variante dédiée. Décision à
   prendre en regardant la structure du `stale-banner` existant.
4. **Type d'event WS** pour les erreurs `ProviderDisabledError` reçues côté
   front : alignement avec la convention existante des erreurs WS.
5. **Forme du retour `OrchestratorRegistry.get(provider)` / `AgentManagerRegistry.get(provider)`**
   pour un provider désactivé (`None`, instance « stopped », ou inchangé) :
   décision purement implementation-side, à prendre quand on saura la
   pression sur les call sites.
6. **Détails de la self-healing back** (§4.2) : précision du flux quand le
   back annule une désactivation (ordre d'opérations, propagation,
   versionnement du payload synced settings). Pas critique mais à formaliser.

---

## 9. Risques / points de vigilance

### 9.1 Race condition « toggle vs nouvelle session »

L'utilisateur clique *Disable* pendant qu'un autre device crée une session.
Le garde-fou §4.2 prend le relais : le back annule la désactivation et
broadcast la valeur corrigée. UX un peu surprenante (switch revient sur *ON*)
mais comportement correct.

### 9.2 Édition manuelle de `settings.json`

Si l'utilisateur édite à la main pour mettre tous les providers en
`disabledProviders`, le dialogue initial reprend la main au prochain
rechargement du front. Pas d'état piégé.

### 9.3 Upgrade depuis une version sans la clé

Au premier démarrage de la version qui contient cette feature, `settings.json`
existe mais ne contient pas `disabledProviders` → dialogue initial → choix
utilisateur → écriture de la clé. À partir de là, comportement normal.

### 9.4 Background compute après réactivation

Cas : Codex désactivé pendant 3 jours, `CURRENT_COMPUTE_VERSION` de Codex a
été bumpé entre temps via une upgrade. À la réactivation, le `start()`
relance le background compute qui rattrape les sessions Codex avec
`compute_version` outdated. Comportement standard, rien à coder en plus.

### 9.5 Codex et `~/.codex/config.toml`

Désactiver Codex dans TwiCC ne désinstalle pas le plugin TwiCC du
`config.toml`. Conséquence : `codex` lancé en standalone continuera de voir
les skills `$twicc-*`. Si TwiCC est complètement désinstallé, l'utilisateur
peut nettoyer via `codex plugin uninstall twicc@twicc`. Décision actée :
acceptable.

---

## 10. Hors-scope explicites

Pour fermer la porte aux dérives de scope au moment du plan :

- ❌ Loader / spinner / progress bar pendant l'activation à chaud.
- ❌ Rollback automatique si `start()` lève en arrière-plan.
- ❌ Désinstallation du plugin Codex.
- ❌ Filtrage des sessions affichées par provider actif.
- ❌ Désindexation du search index.
- ❌ Migration de `settings.json` (création automatique de
  `disabledProviders: []` au boot).
- ❌ Mode « provider partiellement actif » (lecture seule mais pas runtime).
  On reste binaire : actif ou désactivé.

---

## 11. Prochaine étape

Une fois ce design validé : passage à `writing-plans` pour produire le plan
d'implémentation détaillé (découpage en PRs, ordre de merge, points de
vérification).
