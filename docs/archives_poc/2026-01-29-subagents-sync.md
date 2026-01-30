# Spec: Synchronisation des Subagents

## Contexte

Les sessions Claude Code peuvent lancer des subagents (sous-sessions autonomes). Ces subagents sont stockés dans des fichiers JSONL avec exactement le même format que les sessions principales, mais dans un emplacement différent.

### Structure des fichiers

```
~/.claude/projects/{project_id}/
├── {session_id}.jsonl                              # Session principale
├── {session_id}/                                   # Dossier de la session
│   └── subagents/
│       ├── agent-a6c7d21.jsonl                     # Subagent 1
│       ├── agent-af6f5eb.jsonl                     # Subagent 2
│       └── ...
├── agent-xxx.jsonl                                 # IGNORER (ancien format)
└── ...
```

**Important :** Les fichiers `agent-*.jsonl` directement au niveau du projet (ancien format) doivent être ignorés. Seuls les subagents dans `{session_id}/subagents/` sont pris en compte.

### Format JSONL des subagents

Identique aux sessions principales. La première ligne contient des métadonnées spécifiques :

```json
{
    "sessionId": "e9cce630-bbb5-42a7-9a2d-25bc38740935",
    "agentId": "a6c7d21",
    "isSidechain": true,
    "slug": "gleaming-marinating-twilight",
    "type": "user",
    "message": { ... },
    ...
}
```

- `sessionId` : ID de la session parente
- `agentId` : Identifiant court du subagent (correspond au nom du fichier sans `agent-` ni `.jsonl`)

---

## Objectif

Synchroniser les fichiers JSONL des subagents en base de données, de la même manière que les sessions principales :
- Au démarrage via `sync_all()` / `sync_project()`
- En temps réel via le file watcher

**Hors scope (pour l'instant) :**
- Exposition des subagents via l'API REST
- Envoi des subagents au frontend via WebSocket
- Affichage des subagents dans l'interface

---

## Modifications

### 1. Modèle `Session` (models.py)

Ajouter les champs suivants :

```python
class SessionType(models.TextChoices):
    SESSION = "session", "Session"
    SUBAGENT = "subagent", "Subagent"

class Session(models.Model):
    # Champs existants...

    # Nouveaux champs
    type = models.CharField(
        max_length=20,
        choices=SessionType.choices,
        default=SessionType.SESSION,
        db_index=True,
    )
    parent_session = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subagents',
    )
    agent_id = models.CharField(
        max_length=36,  # Même longueur que session.id
        null=True,
        blank=True,
    )
```

**Notes :**
- `type` : Distingue sessions normales et subagents
- `parent_session` : Référence vers la session parente (null pour les sessions normales)
- `agent_id` : L'identifiant court extrait du nom de fichier (ex: `a6c7d21`)
- Pas de champ `slug` (non nécessaire)

### 2. Synchronisation (`sync.py`)

#### 2.1 Modification de `sync_project()`

Ordre de traitement :
1. Parcourir les fichiers JSONL de sessions (comportement actuel)
2. **Nouveau :** Pour chaque session traitée, vérifier si `{session_id}/subagents/` existe
3. **Nouveau :** Si oui, traiter tous les `agent-*.jsonl` de ce dossier
4. Passer à la session suivante

```python
def sync_project(project_id: str, on_session_progress=None) -> dict:
    # ... code existant pour lister les sessions ...

    for session_file in session_files:
        # Sync de la session (existant)
        sync_session(session_file, ...)

        # Nouveau : sync des subagents de cette session
        subagents_dir = session_file.parent / session_file.stem / "subagents"
        if subagents_dir.is_dir():
            for subagent_file in subagents_dir.glob("agent-*.jsonl"):
                sync_subagent(subagent_file, parent_session=session, ...)
```

#### 2.2 Nouvelle fonction `sync_subagent()`

```python
def sync_subagent(
    file_path: Path,
    parent_session: Session,
    project: Project,
) -> tuple[list[int], list[int]]:
    """
    Synchronise un fichier subagent JSONL.

    Args:
        file_path: Chemin vers le fichier agent-xxx.jsonl
        parent_session: Session parente
        project: Projet parent

    Returns:
        (new_line_nums, modified_line_nums)
    """
    agent_id = file_path.stem.removeprefix("agent-")

    session, created = Session.objects.get_or_create(
        id=agent_id,
        defaults={
            "project": project,
            "type": SessionType.SUBAGENT,
            "parent_session": parent_session,
            "agent_id": agent_id,
        }
    )

    # Réutiliser sync_session_items() - format JSONL identique
    return sync_session_items(session, file_path)
```

#### 2.3 Modification de `is_session_file()`

```python
def is_session_file(path: Path) -> bool:
    """Retourne True si c'est un fichier session (pas un subagent)."""
    return (
        path.suffix == ".jsonl"
        and not path.name.startswith("agent-")
    )

def is_subagent_file(path: Path) -> bool:
    """Retourne True si c'est un fichier subagent dans le bon emplacement."""
    return (
        path.suffix == ".jsonl"
        and path.name.startswith("agent-")
        and path.parent.name == "subagents"
    )
```

### 3. File Watcher (`watcher.py`)

#### 3.1 Règles de filtrage

**Chemins valides :**
- Session : `{project_id}/{session_id}.jsonl` (2 parties)
- Subagent : `{project_id}/{session_id}/subagents/agent-xxx.jsonl` (4 parties)

**Chemins ignorés :**
- `{project_id}/agent-*.jsonl` (ancien format, 2 parties mais commence par `agent-`)
- Tout autre structure

```python
def parse_jsonl_path(path: Path, projects_dir: Path) -> ParsedPath | None:
    """
    Parse un chemin JSONL et détermine son type.

    Returns:
        ParsedPath(project_id, session_id, type, parent_session_id) ou None
    """
    relative = path.relative_to(projects_dir)
    parts = relative.parts

    if len(parts) == 2:
        # Format: project_id/xxx.jsonl
        project_id, filename = parts
        if filename.startswith("agent-"):
            return None  # Ignorer les anciens agents au niveau projet
        if filename.endswith(".jsonl"):
            session_id = filename.removesuffix(".jsonl")
            return ParsedPath(project_id, session_id, SessionType.SESSION, None)

    elif len(parts) == 4:
        # Format: project_id/session_id/subagents/agent-xxx.jsonl
        project_id, parent_session_id, subdir, filename = parts
        if subdir == "subagents" and filename.startswith("agent-") and filename.endswith(".jsonl"):
            agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
            return ParsedPath(project_id, agent_id, SessionType.SUBAGENT, parent_session_id)

    return None
```

#### 3.2 Modification de la boucle principale

```python
async for changes in awatch(projects_dir):
    for change_type, path_str in changes:
        path = Path(path_str)

        if not path_str.endswith(".jsonl"):
            continue

        parsed = parse_jsonl_path(path, projects_dir)
        if parsed is None:
            continue

        if parsed.type == SessionType.SESSION:
            # Comportement existant : sync + broadcast
            await sync_and_broadcast(path, change_type, channel_layer)

        elif parsed.type == SessionType.SUBAGENT:
            # Nouveau : sync SANS broadcast
            await sync_subagent_no_broadcast(path, parsed, change_type)
```

#### 3.3 Nouvelle fonction `sync_subagent_no_broadcast()`

```python
async def sync_subagent_no_broadcast(
    path: Path,
    parsed: ParsedPath,
    change_type: Change,
) -> None:
    """
    Synchronise un subagent sans envoyer de message WebSocket.
    """
    if change_type == Change.deleted:
        await Session.objects.filter(
            id=parsed.session_id,
            type=SessionType.SUBAGENT,
        ).aupdate(archived=True)
        return

    # Récupérer ou créer la session parente
    parent_session = await Session.objects.filter(id=parsed.parent_session_id).afirst()
    if parent_session is None:
        # Session parente pas encore sync, ignorer pour l'instant
        # Elle sera traitée au prochain sync
        return

    project = await Project.objects.filter(id=parsed.project_id).afirst()
    if project is None:
        return

    # Sync du subagent
    await sync_to_async(sync_subagent)(path, parent_session, project)
```

### 4. Compute Background

Si des tâches de compute background existent et déclenchent des broadcasts, s'assurer qu'elles filtrent les subagents :

```python
# Dans toute fonction qui broadcast après compute
if session.type == SessionType.SUBAGENT:
    return  # Pas de broadcast pour les subagents
```

### 5. Vérification : Pas de fuite vers le frontend

Points de contrôle pour s'assurer que les subagents ne sont pas envoyés au frontend :

1. **`sync_and_broadcast()`** : Ne doit traiter que les sessions (vérifié par `parse_jsonl_path`)
2. **`sync_subagent_no_broadcast()`** : Ne fait aucun broadcast par design
3. **Compute background** : Filtre sur `session.type`
4. **API REST** : Hors scope, mais si elle liste les sessions, filtrer sur `type=SESSION`

---

## Migration

```python
# 0003_session_subagent_fields.py

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_previous_migration'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='type',
            field=models.CharField(
                choices=[('session', 'Session'), ('subagent', 'Subagent')],
                db_index=True,
                default='session',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='session',
            name='parent_session',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subagents',
                to='core.session',
            ),
        ),
        migrations.AddField(
            model_name='session',
            name='agent_id',
            field=models.CharField(blank=True, max_length=36, null=True),
        ),
    ]
```

---

## Tests manuels

1. **Sync initial :**
   ```bash
   uv run python -c "from twicc_poc.sync import sync_all_with_progress; sync_all_with_progress()"
   ```
   Vérifier que les subagents sont créés en DB avec le bon `type` et `parent_session`.

2. **Sync live :**
   - Lancer une session Claude Code qui crée un subagent
   - Vérifier que le subagent apparaît en DB
   - Vérifier qu'aucun message WebSocket n'est envoyé pour le subagent

3. **Vérification DB :**
   ```sql
   SELECT id, type, parent_session_id, agent_id FROM core_session WHERE type = 'subagent';
   ```

---

## Travail futur (hors scope)

- Exposer les subagents via l'API REST
- Afficher les subagents dans l'interface (arborescence, inline, etc.)
- Calculer les coûts agrégés (session + subagents)
