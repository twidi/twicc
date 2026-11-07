Ein Worktree ist eine zweite Arbeitskopie desselben Repositorys, ausgecheckt auf einem
**anderen Branch**, in einem **eigenen Ordner** – mit gemeinsamem Verlauf des Repositorys. Damit kannst du
(oder ein Agent) an einem Feature-Branch arbeiten, ohne deinen Haupt-Checkout anzufassen.

In TwiCC erscheint ein Worktree als **eigenes** [Projekt](help/projects),
automatisch mit dem Haupt-Repository verknüpft. Seine Sitzungen, Kosten und Aktivität **fließen in die Ansicht des
übergeordneten Projekts ein**, sodass du alles zusammen siehst statt getrennt.

### Wo Worktrees erstellt werden

Die Einstellung **Worktree-Verzeichnis** legt fest, wo TwiCC neue Worktrees erstellt,
und füllt den Pfad vor, wenn du einen erstellst. Die globale Einstellung ist ein **Template**
mit Platzhaltern, die pro Projekt aufgelöst werden – `{git_root}` (sein Git-Stammverzeichnis),
`{project_name}` (sein Name oder, falls unbenannt, der Ordnername) und `{project_basedir}`
(sein Ordnername) –, sodass `{git_root}/.worktrees` die Worktrees in jedem Repository hält,
während `/home/me/worktrees/{project_name}` alle an einem Ort sammelt. Jedes
Projekt kann sie mit einem eigenen absoluten Pfad überschreiben – die globale Einstellung legst du in den
Einstellungen fest, die eines Projekts in dessen Bearbeiten-Dialog.

### Einen erstellen

Du erstellst einen Worktree, während du eine neue Sitzung darin startest. In einem Menü **Neue Sitzung**
hat jede Projektzeile rechts eine Worktree-Schaltfläche – ein Branch mit einem **+**.
Klicke darauf, um den Worktree-Dialog zu öffnen. (Die Schaltfläche erscheint nur bei
Projekten, die Git-Repositorys sind.)

Im Dialog:

- **Neuer Worktree**: TwiCC erstellt den Worktree und seinen Branch am gewählten
  Ort.
- **Vorhandener Worktree**: übernimmt einen Worktree, der bereits auf der Festplatte liegt und den TwiCC noch
  nicht kennt (der Tab „Vorhandener Worktree“).

In beiden Fällen kannst du sofort eine Entwurfssitzung im neuen Worktree starten.

### Aufräumen

Ein Worktree erscheint weiter in der Projektauswahl und in den Menüs „Neue Sitzung“, bis
du ihn **archivierst** – im Bearbeiten-Dialog oder über sein **⋮**-Menü in der Projektauswahl
oben in der Seitenleiste (Archivieren blendet ihn aus, ohne etwas zu löschen). Das räumt ihn jedoch nur
aus TwiCC auf: Den Worktree wirklich zu beenden – den Branch zu mergen und ihn auf der Festplatte zu entfernen – liegt bei dir oder deinem Agenten;
TwiCC übernimmt das nicht.
