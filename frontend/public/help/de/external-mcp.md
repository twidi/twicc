Mit externem MCP kann ein Agent außerhalb von TwiCC mit deiner Instanz arbeiten. Du kannst zum
Beispiel einen MCP-Client verbinden, um Projekte einzusehen, Sitzungen zu erstellen oder Nachrichten
mit Bildern zu senden.

TwiCC bleibt eine Single-User-Anwendung. Du genehmigst jede Verbindung zu deiner Instanz. Für den
verbindenden Client legst du kein weiteres Nutzerkonto an.

### Interner und externer MCP

Agenten, die in TwiCC laufen, haben bereits eine interne MCP-Verbindung. Sie brauchen weder diese
Einrichtung noch eine OAuth-Genehmigung. Wenn du externen MCP deaktivierst, bleibt ihr interner
Zugriff verfügbar.

Externe Clients verbinden sich über eine eigene öffentliche Adresse. Sie erhalten nach deiner
Genehmigung über OAuth Zugriff.

### Was eine Verbindung kann

Eine genehmigte Verbindung hat vollen Zugriff auf die externen MCP-Tools. Sie kann deine TwiCC-Daten
lesen und ändern, Projekte und Sitzungen verwalten, Nachrichten senden und Freigaben verwalten. Es
gibt keine Berechtigungsauswahl pro Verbindung.

Die Genehmigung gilt für die Verbindung, nicht für jeden Tool-Aufruf. Der Client kann handeln, ohne
dass TwiCC für jede erstellte Nachricht oder Sitzung erneut genehmigen lässt.

Ein externer Client ist keine TwiCC-Sitzung. Er kann weder `whoami` noch `self` oder `parent`
verwenden, um eine aufrufende Sitzung zu identifizieren. Er muss explizite Sitzungs-IDs angeben, wo
ein Tool eine Sitzung braucht. Sitzungsbezogene Operationen setzen diesen Kontext voraus; sie können
ihn nicht aus der externen Verbindung ableiten.

### Anhänge

Ein externer Client hängt eine Datei seines eigenen Rechners über einen Base64-Data-URI in `attach`
an: `data:<mime>;name=<percent-encoded file name>;base64,<data>`. `name=` behält den Dateinamen.
Inline-Daten sind auf 50 MiB pro Befehl begrenzt. Lege eine größere Datei bei einem
Datei-Speicherdienst ab und gib ihre URL im Nachrichtentext an, oder gib einen absoluten Pfad zu einer
Datei an, die bereits auf dem TwiCC-Host liegt. Eine Peer-Nachricht sendet jede Datei inline, deshalb
funktioniert dort nur die URL.

### Vor dem Verbinden

Starte TwiCC mit einem Passwort. Externer MCP lässt sich ohne Passwort nicht aktivieren.
Wenn du TwiCC ohne Passwort neu startest, deaktiviert TwiCC externen MCP und behält die vorhandenen
Autorisierungen. Die eigene URL bleibt gespeichert. Aktiviere externen MCP nach dem Wiederherstellen
eines Passworts erneut. Clients können dann mit ihren vorhandenen Zugangsdaten fortfahren, vorbehaltlich
ihres normalen Ablaufs. Bereits widerrufene Autorisierungen bleiben widerrufen.

Um ein Passwort zu setzen, führe Folgendes aus:

```
<twicc> password set
```

Nutze für `<twicc>` die Art, wie du TwiCC üblicherweise startest. Starte TwiCC neu, nachdem du das
Passwort gesetzt hast.

Öffne **Einstellungen → Externer MCP**. Gib deine **Dedizierte MCP-URL** ein und wähle dann
**Anwenden**. Gib den HTTPS-Origin ein, etwa `https://mcp.example.com`, ohne `/mcp`. Aktiviere dann
den **externen MCP-Zugriff**.

Nutze einen eigenen Hostnamen, der sich von deinen Adressen für Extern, Freigabe und Peers
unterscheidet. Dieser Hostname bedient nur MCP und seine OAuth-Endpoints. Er bedient weder deine
TwiCC-Oberfläche noch Dateien oder andere Anwendungsrouten.

Die Adresse muss vom Client aus erreichbar sein. MCP-Aufrufe laufen von Maschine zu Maschine. Wenn
dein üblicher Tunnel Besucher auffordert, sich per E-Mail, SSO oder einer anderen interaktiven Prüfung
anzumelden, nutze ihn nicht für MCP. Der Client kann diese Prüfung nicht abschließen.

Erstelle eine separate Tunnel-Adresse ohne diese Schranke und richte sie auf deinen TwiCC-Server.
Lass den Schutz auf deiner üblichen TwiCC-Adresse aktiv. TwiCC schützt die MCP-Adresse mit OAuth und
deiner Verbindungsgenehmigung.

Die allgemeine Einrichtung von Tunnel und Passwort findest du unter [TwiCC von überall
erreichen](help/external-url).

### Einen Client verbinden

Kopiere die **MCP-URL** aus den Einstellungen und trage sie in der Remote-MCP-Konfiguration deines
Clients ein. Diese Adresse enthält `/mcp`, zum Beispiel `https://mcp.example.com/mcp`.

Nutze einen Client, der Remote-MCP mit OAuth unterstützt. Die genauen Bedienelemente der Einrichtung
hängen vom Client ab. TwiCC liefert die Autorisierungsinformationen über standardisierte
MCP-Discovery-Endpoints.

Desktop-Clients können den Browser-Callback auf `http://127.0.0.1` oder `http://[::1]` mit einem bei
jeder Verbindung anderen freien Port empfangen. Registrierte Adresse, Pfad und Query müssen weiterhin
übereinstimmen. Diese Port-Ausnahme gilt nicht für Remote-HTTPS-Callbacks oder den Hostnamen
`localhost`.

Starte die Verbindung im Client. Er öffnet eine Autorisierungsseite mit einem Verifizierungscode.
Lass diese Seite geöffnet, bis die Verbindung abgeschlossen ist.

TwiCC zeigt auf deinen angemeldeten Geräten eine dauerhafte Benachrichtigung über die
Verbindungsanfrage. Wähle **Prüfen** und gib dann den Code aus dem verbindenden Browser ein. Der Code
verknüpft deine Genehmigung mit der Anfrage, die du gestartet hast.

Du kannst auf einem anderen Gerät genehmigen. Der Browser, der die Verbindung gestartet hat,
schließt den OAuth-Ablauf trotzdem ab. Die Benachrichtigung verschwindet auf deinen anderen Geräten,
sobald die Anfrage erledigt ist.

Prüfe die Client-Details, bevor du **Autorisieren** wählst. Wähle **Ablehnen**, wenn du die Anfrage
nicht gestartet hast. Anfragen laufen nach zehn Minuten ab; starte im Client neu, wenn die Anfrage
abläuft.

### Namen und Nachrichten-Header

Du kannst jeder Verbindung einen optionalen Namen geben, etwa **ChatGPT** oder
**Desktop-Assistent**. Du kannst ihn später in den Verbindungsdetails ändern.

Beim Prüfen enthält das Namensfeld zunächst den Namen, den der Client für sich angibt. Bestätige ihn,
ändere ihn oder lösche ihn, bevor du autorisierst. TwiCC verwendet nur den Namen, den du genehmigt
hast: Ein Client kann sich in deinen Unterhaltungen nicht selbst benennen.

Nachrichten, die über diese Verbindung gesendet werden, tragen einen Header wie **Message via
ChatGPT**. Ohne Namen lautet der Header **Message via external MCP**. Der Header identifiziert die
Verbindung, nicht eine andere TwiCC-Sitzung.

Die **Client-ID** in den Details ist ein technischer Bezeichner. Sie ist weder ein Zugriffstoken noch
der Verifizierungscode. Du musst sie nicht kopieren, um eine Anfrage zu genehmigen.

### Verbindungen verwalten

Öffne **Einstellungen → Externer MCP → Verbindungen verwalten**. Die Liste zeigt drei Abschnitte,
sofern sie Einträge enthalten:

- **Ausstehende Anfragen** — Anfragen, die auf deine Entscheidung warten;
- **Aktiv** — genehmigte Verbindungen, auch solche, die OAuth noch abschließen;
- **Widerrufen** — Verbindungen, die keinen Zugriff mehr haben.

Wähle **Prüfen**, um eine Anfrage einzusehen, oder **Details**, um eine bestehende Verbindung
einzusehen. Das Schließen der Details führt zur Liste zurück, wenn du sie dort geöffnet hast. Öffnest
du eine Anfrage über ihre Benachrichtigung, gelangst du direkt zu ihren Details; das Schließen dieses
Dialogs öffnet die Liste nicht.

Mehrere Clients können sich unabhängig verbinden. Das Widerrufen einer Verbindung widerruft die
anderen nicht.

### Zugriff widerrufen

Wähle **Widerrufen**, damit eine Verbindung keine weiteren MCP-Aufrufe mehr ausführen kann. Ihre
vorhandenen Zugangsdaten gewähren keinen Zugriff mehr. Das Widerrufen macht bereits erledigte Arbeit
nicht rückgängig und stoppt keine Sitzungen, die der Client zuvor gestartet hat.

Die widerrufene Verbindung bleibt in der Liste. Um dich erneut zu verbinden, starte im Client eine neue
Verbindung und genehmige die neue Anfrage.

Wenn du die dedizierte MCP-URL änderst oder den externen Zugriff deaktivierst, widerruft das die
bestehenden Verbindungen. Konfiguriere die neue Adresse in deinen Clients und verbinde dich erneut.

### Schutz vor OAuth-Missbrauch

TwiCC begrenzt neue OAuth-Anfragen pro Client und pro Netzwerkquelle. Ein Client kann drei ausstehende
Anfragen haben, eine Quelle zehn. Eine Quelle kann zwanzig registrierte Clients halten, die nie eine
Autorisierung erhalten haben.

Wiederholte Ablehnungen bei der Zulassung oder ungewöhnlich hoher Zulassungsverkehr lösen eine
zehnminütige Pause für neue Registrierungen und Autorisierungsanfragen aus. Der Eigentümer erhält eine
Benachrichtigung **Verdacht auf OAuth-Missbrauch**. Einstellungen und Verbindungsverwaltung zeigen den
Grund, aktuelle Zähler und die verbleibende Pausenzeit.

Die Pause lässt bestehende MCP-Verbindungen, Token-Erneuerungen, Widerruf und bereits laufende
Autorisierungsanfragen verfügbar. Sie läuft automatisch ab. Die Warnung bleibt bestehen, bis du sie
verwirfst. Hoher Verkehr ist ein Verdacht auf Missbrauch, kein Beweis für einen Angriff. Mehrere
Clients können sich eine Netzwerkquelle teilen.

Wähle **Gesamten externen Zugriff aussetzen**, um alle neuen externen MCP- und OAuth-Anfragen zu
stoppen, ohne bestehende Autorisierungen zu widerrufen. Bereits laufende Anfragen dürfen enden. Um
fortzufahren, aktiviere den externen Zugriff in den Einstellungen erneut. Diese manuelle
Aussetzung bleibt über Neustarts hinweg bestehen.

Verkehrszähler, Warnungen und automatische Pausen werden beim Neustart von TwiCC zurückgesetzt. Die
Kontingente für Registrierungen und ausstehende Anfragen bleiben gespeichert. Der normale Ablauf von
Token und Anfragen gilt während einer Aussetzung weiter.

Die Ratenbegrenzung nutzt die Client-Adresse, die der ASGI-Server liefert. Ein Proxy muss die echte
Client-Adresse weitergeben und vom Server als vertrauenswürdig eingestuft sein; TwiCC vertraut selbst
nie beliebigen Forwarding-Headern. IPv6-Adressen im selben /64 teilen sich ein Quellkontingent.
Gespeichert wird nur ein Hash der Quelle mit Schlüssel, nicht die IP-Adresse.

### Wenn eine Verbindung nicht zustande kommt

Prüfe, ob externer MCP aktiviert ist und der Client die kopierte URL verwendet, einschließlich `/mcp`.
Prüfe, ob der dedizierte Hostname diese TwiCC-Instanz erreicht und keine Anmeldeseite des
Tunnel-Anbieters zeigt.

Lass den verbindenden Browser geöffnet, während du genehmigst. Wenn die Liste **verbindet** zeigt, war
die Genehmigung erfolgreich, aber der Client hat OAuth noch nicht abgeschlossen. Wenn die Anfrage
abläuft, starte im Client eine neue Verbindung.

### Batch-Befehle

Nutze `batch_read`, um unabhängige Lesezugriffe in einem MCP-Aufruf zu bündeln. Standardmäßig führt es
bis zu vier Befehle gleichzeitig aus. Setze `mode` auf `sequential`, um sie nacheinander auszuführen.
Nutze `batch` für Befehle in fester Reihenfolge, auch für Änderungen. Es läuft immer sequenziell.
Dieselben Tools stehen Agenten zur Verfügung, die in TwiCC laufen.

Ermittle zuerst das Schema jedes Befehls. Gib seinen normalen MCP-Namen und seine Argumente an:

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {"id": "projects", "name": "projects", "arguments": {}}
  ]
}
```

Jeder Aufruf braucht eine eindeutige `id`, einen Befehls-`name` und ein `arguments`-Objekt. TwiCC
validiert den gesamten Batch, bevor ein Befehl startet. Ein ungültiger Aufruf lehnt den Batch ab, ohne
seine anderen Befehle auszuführen. Die normalen Berechtigungsprüfungen gelten weiterhin für jeden
Befehl. Externe Aufrufe erfordern weiterhin explizite Sitzungs-IDs.

Die Ergebnisse kommen gemeinsam, in Eingabereihenfolge. Jeder Eintrag enthält seine ID, den
Ausführungsstatus und die normale Antwort `{exit_code, result, error}` des Befehls. Prüfe das
aggregierte Feld `ok` und jeden Eintrag. Ein abgeschlossener Batch kann fehlgeschlagene oder
übersprungene Befehle enthalten. Ein Befehl kann auch erfolgreich sein, während seine große Antwort
ausgelassen wird (`response_omitted: true`); in diesem Fall ist das aggregierte `ok` false.

`batch` stoppt standardmäßig nach einem Ausführungsfehler. Setze `on_error` auf `continue`, um die
übrigen Befehle trotzdem zu versuchen. `batch_read` nutzt standardmäßig `continue`. Parallele
Ausführung erfordert `continue`; nutze den sequenziellen Modus für `stop`. Das Auslassen von Ausgaben
stoppt nachfolgende Befehle nicht. Abgeschlossene Änderungen bleiben angewendet: Es gibt kein
Rollback, keinen automatischen Retry und keine Übergabe von Ergebnissen an spätere Argumente.
Verschachtelte Batches werden nicht unterstützt.

Ein Batch akzeptiert bis zu 20 Befehle. Beide MCP-Verbindungen teilen sich ein Limit von vier aktiven
Batches und acht aktiven Batch-Befehlen für die gesamte Instanz. Weitere Batches erhalten
`server_busy`. Einzelne Tools unterliegen diesem Zulassungslimit für Batches nicht.
Die gesamte Anfrage ist auf 72 MiB begrenzt, Anhänge eingeschlossen; jeder Befehl akzeptiert höchstens
50 MiB Inline-Anhangsdaten (siehe Anhänge oben für eine größere Datei).
Befehlsantworten über 384 KiB werden ausdrücklich ausgelassen. Das kombinierte Tool-Ergebnis,
einschließlich Text und strukturierter Daten, bleibt unter 16 MiB. Clients können kleinere Limits
haben.

Halte Batches kurz und nutze getrennte Aufrufe für lange Wartezeiten. Die bestehenden
Befehls-Zeitüberschreitungen gelten weiterhin; es gibt keine zusätzliche Zeitüberschreitung für den
Batch. Lange Wartezeiten können die gesamte Batch-Kapazität belegen, auch nachdem der Client seine
Anfrage abgebrochen hat. Ein Abbruch oder eine Zeitüberschreitung beweist nicht, dass Schreibvorgänge
gestoppt haben. Prüfe die betroffenen Ressourcen, bevor du einen unsicheren Schreibvorgang
wiederholst. Batch-IDs identifizieren Aufrufe für die Diagnose; sie verhindern keine doppelte Arbeit.

TwiCC prüft die externe Autorisierung, bevor es jeden Befehl startet. Ablauf, Widerruf oder
deaktivierter externer Zugriff stoppen die übrigen Befehle. Kann TwiCC die Autorisierung nicht
verifizieren, stoppt es sie ebenfalls. Bereits gestartete Befehle dürfen enden, und ihre gesammelten
Ergebnisse bleiben erhalten.
