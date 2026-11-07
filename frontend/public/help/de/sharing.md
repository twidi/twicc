Ein **Freigabelink** ist eine öffentliche, schreibgeschützte URL zu einer deiner Sitzungen oder
Artefakte. Jeder, der den Link hat, kann ihn in einem Browser öffnen – ohne TwiCC-Konto, ohne Anmeldung. Man kann nur **ansehen**: Über einen Link lässt sich nie eine
Nachricht senden, ein Tool ausführen oder bei dir etwas ändern.

## Was du teilen kannst

- **Ein Artefakt** – eine mit Lesezeichen versehene Datei aus dem Artefakte-Tab einer Sitzung,
  einzeln dargestellt (eine HTML-Seite, ein Diagramm, ein Dokument…).
- **Eine Sitzung** – die Unterhaltung, schreibgeschützt dargestellt. Tool-Aufrufe und
  Diffs erscheinen so, wie du sie siehst.

> **Eine Sitzung wird unverändert geteilt** – nichts darin wird geschwärzt oder bereinigt.
> Stelle vor dem Teilen sicher, dass sie keine Geheimnisse oder privaten Daten enthält, und
> gib den Link nur an Personen weiter, denen du vertraust. Ein Artefakt ist eine einzelne Datei, die du
> ausgewählt hast, es gibt also viel weniger zu prüfen.

## Bevor du teilen kannst

Zum Teilen brauchst du einen **eigenen Freigabe-Host** – einen Hostnamen, der sich von dem unterscheidet,
unter dem du TwiCC nutzt, und der auf dieselbe Instanz zeigt. Lege ihn unter
**Einstellungen → Freigabe** fest. Solange er nicht konfiguriert ist, bleiben die Freigabe-Schaltflächen
deaktiviert.

Bedenke, dass **jeder Aufruf von dem Rechner bedient wird, auf dem TwiCC läuft** –
deinem eigenen Computer, über deinen Tunnel. Es gibt keine gehostete Kopie, vermeide es daher,
Links in großem Umfang zu verteilen: Der gesamte Datenverkehr landet direkt auf deinem Rechner.

## Deine Links

Beim Erstellen eines Links kannst du ihm eine **Bezeichnung** geben, ihn mit einem
**Passwort** schützen und ein **Ablaufdatum** festlegen. Du kannst **mehrere Links** zur
selben Sitzung oder zum selben Artefakt erstellen, jeweils mit eigenem Passwort und Ablaufdatum – gib
verschiedenen Personen verschiedene Links und verfolge die Aufruf-Logs jedes Links
getrennt. Ein Link pro Person ist praktisch: Du kannst den Zugriff einer Person widerrufen,
ohne die anderen anzufassen.

Verwalte sie über die Freigabe-Schaltfläche (oder **Einstellungen → Freigabe → Freigegebene
Links**), wo jeder Link auch zeigt, wie oft er geöffnet wurde:

- **Widerrufen** deaktiviert einen Link vorübergehend – Betrachter erhalten einen 404 –, behält aber
  seine Aufruf-Logs, sodass du ihn später wieder aktivieren kannst.
- **Löschen** entfernt ihn dauerhaft, einschließlich seiner Aufruf-Logs.

## Agenten und Freigabelinks

Standardmäßig kannst nur du Freigabelinks erstellen oder verwalten – Agenten nicht.
Zwei Schalter unter **Einstellungen → Freigabe** ändern das, einer pro Art (Sitzungslinks, Artefakt-Links). Beide sind **aus**, bis du sie aktivierst.

Mit aktivierten **Sitzungsfreigaben** können Agenten Sitzungslinks für ihre eigene
Sitzung oder jede Sitzung in ihrem Spawn-Teilbaum erstellen. Mit aktivierten **Artefakt-Freigaben**
können Agenten Artefakt-Links für Lesezeichen erstellen, die ihrer eigenen Sitzung
oder einer beliebigen Sitzung in ihrem Spawn-Teilbaum gehören.

Für die aktivierte Art können Agenten außerdem:

- Links, die sie selbst oder Agenten in ihrem Spawn-Teilbaum erstellt haben, **verwalten** (aktualisieren, löschen, erneut veröffentlichen);
- jeden vorhandenen Link dieser Art **widerrufen** – auch Links, die du selbst
  erstellt hast (Veröffentlichung zurückzunehmen gilt immer als sicher);
- die **URL** jedes vorhandenen Links dieser Art **lesen**, auch deiner.

Agenten können das Passwort eines Links nie entfernen, nie mit dem Anzeigemodus `debug`
teilen, und ihre neuen Sitzungslinks sind eingefrorene Snapshots, sofern sie nicht
ausdrücklich einen Live-Link anfordern.

## Artefakte und Netzwerkzugriff

Ein geteiltes Artefakt läuft für den Betrachter **ohne Zustimmungsabfragen**. Wenn es
Netzwerkaufrufe macht, sind nur die Hosts erreichbar, die **du erlaubt hast**;
jeder andere Host wird blockiert, und der Betrachter sieht einen kurzen Hinweis „vom
Eigentümer blockiert“. Du erlaubst oder verweigerst Hosts über das **Lesezeichen** des Artefakts,
und Hosts, die Betrachter erreichen wollten, werden dort aufgelistet, sodass du sie
nachträglich genehmigen kannst.

Wenn du die Dateien eines Artefakts nach dem Teilen änderst, nutze **Update senden** am
Link, um zu aktualisieren, was Betrachter sehen.
