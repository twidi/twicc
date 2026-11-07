Peer-Messaging verbindet zwei unabhängige TwiCC-Instanzen. Es ist vor allem für zwei Personen gedacht,
die von ihren eigenen Rechnern aus zusammenarbeiten. Du kannst auch zwei eigene Instanzen koppeln,
aber das ist nicht der Hauptanwendungsfall.

Eine Peer-Nachricht gelangt nie von selbst in eine Sitzung auf der anderen Instanz. Der empfangende
Nutzer muss sie zuerst lesen. Er kann sie ablehnen oder in das Eingabefeld einer Sitzung legen.

Auch dann sendet TwiCC die Nachricht nicht an den Agenten. Der Nutzer kann das Eingabefeld prüfen oder
bearbeiten und dann entscheiden, ob er sendet.

### Wofür Peers gedacht sind

Peers geben Agenten einen kontrollierten Weg, Arbeit zwischen Personen weiterzureichen. Ein Agent kann
zum Beispiel Folgendes senden:

- eine Zusammenfassung erledigter Arbeit;
- einen API-Vertrag oder eine technische Frage;
- einen Screenshot, ein Dokument oder eine andere nützliche Datei;
- eine Antwort auf eine frühere Peer-Nachricht.

Du kannst auch selbst eine Nachricht schreiben, wenn die Anweisung ganz von dir stammt und ein Agent
nichts hinzufügen würde.

Die Nachricht muss in sich geschlossen sein. Die andere Instanz teilt weder das Gedächtnis noch die
Unterhaltung der sendenden Sitzung.

### Was Peers nicht teilen

Das Koppeln gibt keiner Person Zugriff auf die andere Instanz. Es teilt keine Projekte, Sitzungen,
Dateien, Terminals, Einstellungen und keine Agentensteuerung.

Nur Nachrichtentitel, Text, Anhänge, Zeit und Antwortbeziehung wandern zwischen den Instanzen. Lokale
Sitzungsdetails bleiben lokal.

### Vor dem Koppeln

Öffne **Einstellungen → Peers**, um deinen Namen und deine Adresse festzulegen. Dein Name
identifiziert deine Instanz in Kopplungsanfragen. Deine Adresse muss vom anderen TwiCC-Rechner aus
erreichbar sein.

Bleibt die Adresse leer, ist Peer-Messaging deaktiviert. Nutze möglichst HTTPS.
Peer-Aufrufe laufen von Maschine zu Maschine. Wenn dein üblicher TwiCC-Tunnel Besucher auffordert, sich
per E-Mail, SSO oder einer anderen interaktiven Prüfung anzumelden, nutze diese Adresse nicht für
Peers. Die andere TwiCC-Instanz kann diese Prüfung nicht abschließen.

Erstelle eine zweite Tunnel-Adresse ohne diese Schranke und nutze sie als deine Peer-Adresse. Lass das
eigene Passwort von TwiCC aktiviert.

Eine Peer-Adresse, die sich von deiner Externen Adresse unterscheidet, bedient nur Peer-Verkehr. Sie
bedient weder Seiten noch Live-Verbindungen, sodass du keinen Pfadfilter im Tunnel brauchst. Gibst du
deine Externe Adresse als Peer-Adresse an, bedient diese einzelne Adresse die App und den
Peer-Verkehr. Jede andere Adresse, auch `localhost`, bedient nie Peer-Verkehr.

Ein Proxy oder Tunnel vor deiner Peer-Adresse muss Anfragekörper von 72 MiB akzeptieren. Einer, der
für das alte Limit von 48 MiB ausgelegt ist, lehnt große Nachrichten ab.

Nutze die dedizierte Freigabe-Adresse nicht für Peers. TwiCC beschränkt diesen Hostnamen auf
Freigabe-Routen, und die Einstellungen lehnen eine Peer-Adresse darauf ab. Die allgemeine Einrichtung
von Tunnel und Passwort findest du unter [TwiCC von überall
erreichen](help/external-url).

### Zwei Instanzen koppeln

Peer-Beziehungen nutzen einen Ablauf aus Anfrage und Genehmigung. Ein Nutzer sendet eine
Kopplungsanfrage über **Peers verwalten**. Der andere Nutzer erhält die Anfrage und sieht einen
sechsstelligen Verifizierungscode.

Der empfangende Nutzer gibt diesen Code über einen Kanal weiter, dem er bereits vertraut, etwa einen
Anruf oder einen privaten Chat. Der Anfragende gibt ihn auf seiner eigenen Instanz ein. Das bestätigt,
dass die Anfrage die vorgesehene Person erreicht hat.

Die Verifizierung schaltet nur die Genehmigung frei. Der empfangende Nutzer entscheidet weiterhin, ob
er die Beziehung annimmt oder ablehnt. Jeder Nutzer wählt außerdem seinen eigenen lokalen Namen für den
Peer.

Nur Nutzer verwalten Peer-Beziehungen. Agenten können sie weder erstellen, genehmigen, ablehnen,
umbenennen, widerrufen noch neu verbinden.

### Eine Nachricht senden

Ein Agent kann aktive Peers auflisten und einem von ihnen eine Nachricht mit Titel senden. Eine
Nachricht kann Text und Dateien beliebigen Typs enthalten; jede Datei behält ihren Namen. Alle Dateien
einer Nachricht sind zusammen auf 50 MiB begrenzt; bei einer größeren Datei legt der Agent sie bei
einem Datei-Speicherdienst ab und gibt ihre URL im Nachrichtentext an. Nachdem der Nutzer die
Peer-Beziehung genehmigt hat, ist auf der Senderseite keine zusätzliche Bestätigung erforderlich.

Ein Peer mit einer älteren TwiCC-Version lehnt eine Nachricht mit Dateien ab: Sein Nutzer muss
aktualisieren. Eine Nachricht nur mit Text erreicht ihn weiterhin.

Du kannst auch selbst eine Nachricht schreiben, über **Peers verwalten**, aus dem Posteingang oder als
Antwort, während du eine Nachricht prüfst. Dieses Formular nimmt nur einen Titel und einen Text auf. Es
enthält keinen Anhang und speichert keinen Entwurf: Schließt du es, ist das Geschriebene verworfen.
Bitte für alles Weitere einen Agenten: Er hängt Dateien an und schreibt die Nachricht für dich.

Der Titel sollte das Thema klar nennen. Der Text sollte den gesamten Kontext enthalten, den die
empfangende Person und ihr Agent brauchen.

Ein erfolgreiches Senden bedeutet, dass die andere Instanz die Nachricht gespeichert hat. Es bedeutet
nicht, dass der empfangende Nutzer sie einer Sitzung zugestellt hat.

### Der Peer-Posteingang

Öffne den Posteingang unter **Einstellungen → Peers** oder über die Posteingang-Schaltfläche neben
**Einstellungen**. Diese Schaltfläche erscheint, sobald das Peer-System eingerichtet ist, auch bei
leerem Posteingang. Braucht eine Anfrage oder Nachricht deine Aufmerksamkeit, zeigt TwiCC zusätzlich
ein Peer-Posteingang-Badge.

Der Posteingang enthält Kopplungsanfragen, Nachrichten zur Prüfung und den Nachrichtenverlauf. Du
kannst Nachrichten nach Peer oder nach ihrem Titel und vollständigen Text filtern.

Eine Benachrichtigung über eine eingehende Nachricht kann den Posteingang öffnen. Sie kann die
Nachricht nicht zustellen und keinen Agenten starten.

TwiCC kann dich auch außerhalb der App benachrichtigen, wenn eine Nachricht oder Kopplungsanfrage
eintrifft — mit einem Ton, einer Browser-Benachrichtigung oder einem Push auf deine Geräte. Schalte das
unter **Einstellungen → Benachrichtigungen → Ein Peer braucht dich** ein. Kein anderes Peer-Ereignis
löst eine Benachrichtigung aus: Sie warten bis zu deinem nächsten Besuch.

### Eine eingehende Nachricht prüfen

Die Prüfung zeigt Absender, Titel, Nachricht, Anhänge und eine eventuelle Antwortbeziehung. Sie zeigt
auch, wenn der Nutzer des Peers die Nachricht selbst statt seines Agenten geschrieben hat. Großer Text
oder große Anhänge erfordern eine zusätzliche Ladeaktion, bevor TwiCC sie rendert oder herunterlädt.

Danach wählst du in der Aktionsliste der Prüfung eine Aktion:

- die Nachricht in das Eingabefeld einer vorhandenen Sitzung legen;
- eine neue Entwurfssitzung erstellen, deren Eingabefeld die Nachricht enthält;
- die Nachricht als erledigt markieren, wenn du sie selbst bearbeitet hast oder nichts zu tun ist —
  kein Agent erhält sie;
- manuell antworten (siehe unten);
- die Nachricht ablehnen.

Das Ablegen einer Nachricht in einem Eingabefeld markiert sie als zugestellt. Es sendet das
Eingabefeld trotzdem nicht und startet den Agenten nicht. Beim Zustellen kannst du eine optionale
Notiz für deinen Agenten hinzufügen. TwiCC hält diese Notiz getrennt von der Nachricht des Peers und
kennzeichnet sie als deinen eigenen Text.

Eine Ablehnung enthält keine Worte. Um eine zu erklären, antworte mit **Manuell antworten** und lehne
im selben Schritt ab.

Der Absender erfährt nur deine erste Entscheidung. Als erledigt markieren und Ablehnen werden daher
nur angeboten, solange die Nachricht aussteht. Eine entschiedene Nachricht lässt sich aus ihrem
Verlaufseintrag weiterhin erneut zustellen.

### Antworten und Sitzungsvorschläge

Eine Peer-Nachricht kann eine frühere Nachricht beantworten. Posteingang und Prüfung zeigen, welche
Nachricht sie beantwortet, und die Prüfung kann diese Nachricht öffnen. Bei einer Nachricht mit
Antworten steht, wer geantwortet hat: der Peer, sein Agent, du oder dein Agent.

Du kannst eine Nachricht selbst beantworten, mit **Manuell antworten** in der Aktionsliste der
Prüfung. TwiCC schlägt den Titel der beantworteten Nachricht mit dem Präfix `Re:` vor. Wartet die
beantwortete Nachricht noch auf deine Entscheidung, kannst du sie im Antwortformular offen lassen, als
erledigt markieren oder im selben Schritt ablehnen. Die Wahl gilt erst, wenn die Antwort den Peer
erreicht hat.

Wenn möglich, schlägt TwiCC die lokale Sitzung vor, die für die frühere Nachricht genutzt wurde. Das
ist nur ein Vorschlag. Der empfangende Nutzer kann eine andere Sitzung oder einen neuen Entwurf
wählen.

Jede Antwort durchläuft dieselbe menschliche Prüfung. Eine Antwort umgeht nie die Genehmigung des
empfangenden Nutzers.

### Nachrichtenstatus

Gesendete Nachrichten haben fünf Zustände:

- **ausstehend** — der empfangende Nutzer hat noch nicht entschieden;
- **zugestellt** — der Nutzer hat die Nachricht in das Eingabefeld einer Sitzung gelegt. Von seinem
  Agenten kann noch etwas folgen;
- **erledigt** — der Nutzer hat die Nachricht selbst bearbeitet. Kein Agent hat sie erhalten, und von
  einem Agenten kommt nichts mehr. Hatte der Nutzer dir etwas mitzuteilen, kam es als Antwort;
- **abgelehnt** — der Nutzer hat die Nachricht abgelehnt;
- **fehlgeschlagen** — der Absender hat von der anderen Instanz keine bestätigte Annahme erhalten.

Der Status hält die erste Entscheidung des empfangenden Nutzers fest. Er kann sie später ändern; der
Absender erfährt davon nichts.

Ein Agent kann den aktuellen Status einer von ihm gesendeten Nachricht prüfen. Der Status
„fehlgeschlagen“ kann unsicher sein: Die andere Instanz hat die Nachricht möglicherweise gespeichert,
bevor die Verbindung ausfiel.

### Erneute Zustellung und Verlauf

Eine entschiedene Nachricht bleibt im Verlauf des Posteingangs verfügbar. Der empfangende Nutzer kann
sie erneut in ein Eingabefeld legen, unabhängig von der früheren Entscheidung. Das hilft, wenn er die
falsche Sitzung gewählt oder einen Entwurf geleert hat.

TwiCC entfernt die Anhangsbytes sieben Tage nach der letzten Entscheidung. Eine neue Entscheidung
startet diese Frist neu, bis die Bytes entfernt sind. Eine ausstehende Nachricht behält ihre Anhänge.
Text, Titel, Anhangsdetails und Antwortbeziehung bleiben immer im Verlauf. Eine spätere erneute
Zustellung kann daher nur noch Text enthalten.

### Widerruf und Neuverbinden

Jede Seite steuert ihre eigene Peer-Beziehung. Jeder Nutzer kann sie jederzeit widerrufen. Der Widerruf
beendet das Messaging und löscht die Zugangsdaten der Beziehung, behält aber den lokalen Peer-Namen
und den vollständigen Nachrichtenverlauf.

Der Widerruf erfolgt still. Die andere Instanz erfährt davon, wenn eine spätere Peer-Anfrage
abgelehnt wird. Ihre Kopie der Beziehung erscheint dann als **defekt**.

Ein Peer kann auch defekt werden, wenn du deine eigene Peer-Adresse änderst oder deaktivierst. Lege
eine erreichbare Adresse fest und verbinde dann jeden betroffenen Peer manuell neu.

Das Neuverbinden nutzt einen neuen Verifizierungscode und erfordert die Genehmigung des anderen
Nutzers, wie die ursprüngliche Kopplung. Es stellt die bestehende Beziehung wieder her, statt eine
weitere zu erstellen, sodass ihr lokaler Name und der Nachrichtenverlauf erhalten bleiben.

Die menschliche Prüfung ist die wichtigste Sicherheitsgrenze. Sie macht nicht vertrauenswürdige
Inhalte nicht sicher. Lies jede Nachricht, bevor du sie in ein Eingabefeld legst.
