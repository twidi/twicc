TwiCC kann **die Chat-Nachrichten laut vorlesen**. Wähle eine Konfiguration unter **Einstellungen →
Sprachausgabe**, teste sie, bestätige, dass du sie gehört hast, und aktiviere sie: Danach erscheint
an den Nachrichten eine Lautsprecher-Schaltfläche.

Es gibt zwei Arten von Konfigurationen:

- **OpenRouter**: ein Sprachmodell von OpenRouter mit seinen Stimmen.
- **Skript**: ein eigener Shell-Befehl, der den Text in Audio umwandelt.

## Neue Zeitachsen-Nachrichten vorlesen

Mit der **Lautsprecher-Schaltfläche neben Zeitachse** liest TwiCC neue Assistenten-Nachrichten
automatisch vor. Dafür ist eine verfügbare Sprachausgabe-Konfiguration erforderlich. Die gedrückte
Schaltfläche bedeutet, dass diese Zeitachse auf neue Nachrichten lauscht.

- Das Lauschen nutzt den Geltungsbereich der Zeitachse: alle Projekte, ein Arbeitsbereich, ein
  Projekt oder ein einzelner Worktree. Stummgeschaltete Sitzungen sind standardmäßig ausgeblendet und
  werden übersprungen. Wähle **Filter → Stummgeschaltete Sitzungen anzeigen**, um sie in dieser
  Zeitachse anzuzeigen und vorzulesen. Andere Anzeigefilter, auch die gewählte Ansicht und der
  Sitzungsfilter, beeinflussen das Lauschen nicht.
- Wenn du **Stummgeschaltete Sitzungen anzeigen** ausschaltest, entfernt TwiCC deren wartende
  Nachrichten. Das bereits gewählte Vorlesen darf zu Ende laufen. Wenn du die Option wieder
  einschaltest, spielt TwiCC die Nachrichten nicht ab, die in der Zwischenzeit übersprungen wurden.
  Manuelles Vorlesen bleibt in stummgeschalteten Sitzungen verfügbar.
- Das Lauschen läuft weiter, wenn du zu einer anderen Seite navigierst, die Anwendungs-Tabs wechselst
  oder eine Sitzung öffnest. Pro Browser-Tab lauscht nur eine Zeitachse.
- Die Zeitachsen-Schaltfläche in der Leiste zeigt einen kleinen Lautsprecher, solange das Lauschen
  aktiviert ist. Ihre Bezeichnung nennt die lauschende Zeitachse. Ein Klick öffnet diese Zeitachse,
  ohne das Lauschen zu ändern.
- Nachrichten müssen ein natives Nachrichtendatum haben, das **strikt nach dem Klick auf die
  Aktivierung** liegt. Der vorhandene Verlauf wird nicht abgespielt, wenn du das Lauschen aktivierst.
- Nachzügler bleiben bis zu **fünf Sekunden** hinter der neuesten Nachricht zulässig, deren Vorlesen
  beginnt. Genau fünf Sekunden sind noch zulässig. Akzeptierte Nachrichten warten in der Warteschlange
  auf ihren Durchgang.
- Manuelles Vorlesen hat Vorrang. Das automatische Vorlesen wartet, solange eine manuelle Anfrage
  oder Wiedergabe läuft. Der Zeitachsen-Lautsprecher in der Leiste bleibt während dieser Wartezeit und
  zwischen den Nachrichten sichtbar.
- Klicke auf den gedrückten Zeitachsen-Lautsprecher, um das Lauschen zu deaktivieren und seine
  Warteschlange zu leeren. Das stoppt das automatische Vorlesen und lässt unabhängiges manuelles
  Vorlesen weiterlaufen. Wenn du eine andere Zeitachse aktivierst, leert TwiCC die Warteschlange der
  vorherigen.
- Die vorhandene Schaltfläche **Vorlesen stoppen** in der Leiste und die Schaltfläche **Vorlesen
  stoppen** der Nachricht stoppen das aktuelle Vorlesen. Beim automatischen Vorlesen deaktivieren sie
  außerdem das Lauschen und leeren die Warteschlange.
- Das Lauschen und seine Warteschlange bleiben im Speicher. Ein Neuladen der Seite löscht sie.

Das automatische Vorlesen nutzt die aktive Sprachausgabe-Konfiguration. Unter **Einstellungen →
Zusammenfassungen** steuert **Zusammenfassungen in der Zeitachsen-Sprachausgabe vorlesen** die
automatischen Zusammenfassungen. Die Option ist standardmäßig an. Berechtigte Nachrichten enthalten
mindestens **1.000 Inhaltszeichen**. Kürzere Nachrichten nutzen die vorhandene Einstellung zur
Textanpassung. Ansagen zu Projekten und Sitzungen behalten ihren üblichen Wortlaut.

TwiCC bereitet bis zu zwei wartende Textzusammenfassungen vor, während ein anderes Vorlesen läuft.
Audio erzeugt es erst, wenn eine Nachricht an der Reihe ist. Ein gewähltes Vorlesen behält seine
Einstellungen, bis es endet. Wenn du den Schalter ausschaltest, gibt TwiCC nicht mehr benötigte
Vorbereitungen frei, und ausstehende Nachrichten verwenden ihre Originale. Eine fehlgeschlagene
Zusammenfassung zeigt einen Fehler und überspringt diese Nachricht. Das Lauschen geht mit der
nächsten Nachricht weiter.

## Eine Nachricht oder Auswahl zusammenfassen

Nutze **Zusammenfassen** bei einer berechtigten Assistenten-Nachricht im Chat oder in einer
Nachrichtenansicht der Zeitachse. Der Schwellenwert zählt die vollständige logische Nachricht,
einschließlich ihrer Code- und Textblöcke. Eingebettete Artefakt-Payloads und Steuerungs-Markup
zählen nicht mit. Die Zusammenfassung umfasst etwa ein Drittel der Quelle, höchstens 140 Wörter.

Nachrichtenzusammenfassungen werden gespeichert. Chat, Zeitachsenansichten und automatisches Lauschen
teilen sich dasselbe Ergebnis, wenn Quelle und Modellrichtlinie übereinstimmen. Auch die automatische
Vorbereitung speichert erfolgreiche Zusammenfassungen. Ein Ergebnis nach einer älteren Richtlinie
bleibt verfügbar. Eine neue Erzeugung ersetzt ein Ergebnis erst nach Erfolg.

Mit **Zusammenfassung anzeigen** wechselst du zwischen dem Originaltext und seiner Zusammenfassung.
Kopieren und **Vorlesen** nutzen diese Ansicht. Die Original-Sprachausgabe im Chat behält ihren
ursprünglichen Blockbereich. Die Original-Sprachausgabe der Zeitachse nutzt den zusammengesetzten
Snapshot der Zeitachse. Manuelle Sprachausgabe unterbricht die automatische und funktioniert
unabhängig vom Schalter für automatische Zusammenfassungen. Die üblichen Ansagen zu Projekten und
Sitzungen kennzeichnen eine Zusammenfassung nie.

Markiere Text und wähle **Zusammenfassen** in den Auswahlaktionen für eine temporäre Zusammenfassung.
Ihr Dialog unterstützt Kopieren, Vorlesen, Erneut versuchen, Abbrechen und Schließen. Das Schließen
des Dialogs gibt seine Erzeugung frei und stoppt das zugehörige Vorlesen. Auswahlzusammenfassungen und
der Test in den Einstellungen werden nie als Nachrichtenzusammenfassungen gespeichert.

Der Zusammenfassungstext erscheint während des Streamings. Die Sprachausgabe startet mit den
verfügbaren Teilen der Zusammenfassung, ohne zweiten Anpassungsaufruf. Schlägt die Erzeugung nach dem
Start der Sprachausgabe fehl, stoppt die Wiedergabe mit einem Fehler. TwiCC spielt weder das Original
erneut ab noch speichert es eine Teilzusammenfassung.

### Zusammenfassungsmodell

Wähle unter **Einstellungen → Zusammenfassungen** zwischen **Auto**, **Claude Haiku 5.5**,
**GPT-6 Luna** und **OpenRouter**. Auto wählt einen aktivierten laufenden Agenten, zuerst Claude,
dann Codex. Ist der gewählte Agent nicht verfügbar, weicht TwiCC auf den anderen Agenten aus.

OpenRouter nutzt das vorhandene OpenRouter-Textmodell und den vorhandenen Schlüssel. Die Aufrufe
werden über dein OpenRouter-Konto bezahlt. Ohne Schlüssel oder über **60.000 Quellzeichen** wählt
TwiCC einen aktivierten Agenten. Dabei bleibt die vollständige Quelle erhalten.
Schlägt OpenRouter fehl, bevor die Zusammenfassungs-Sprachausgabe festgelegt ist, kann ein
aktivierter Agent den vorläufigen Text ersetzen. Ohne verfügbares Modell zeigt das Zusammenfassen
einen Fehler. Der automatische Zeitachsen-Schalter deaktiviert manuelle Zusammenfassungen nicht.

## Testen, bestätigen, aktivieren

**Test** liest den Testtext mit der Konfiguration vor und fragt dann **„Hast du es richtig
gehört?“**. Antworte mit **Ja**, um die Konfiguration zu validieren. Nur eine validierte
Konfiguration lässt sich aktivieren, und die aktive Konfiguration ist die, die die Nachrichten nutzen.

Wenn du änderst, was den Ton erzeugt (das Modell, die Stimme, das Format, den Befehl …), verliert die
Konfiguration ihre Validierung: Teste sie erneut.

## OpenRouter-Konfigurationen

Sie benötigen einen OpenRouter-Schlüssel: Füge `TWICC_OPENROUTER_API_KEY` zur Datei `.env` in deinem
TwiCC-Datenverzeichnis hinzu und starte TwiCC dann neu. Der Schlüssel verlässt den Server nie.

Die angezeigten Preise sind die Listenpreise von OpenRouter. Nach einem Test zeigt die Konfiguration,
was die Sprachausgabe des Tests tatsächlich gekostet hat.

## Lange Nachrichten

Eine lange Nachricht wird **in Teilen** vorgelesen, nacheinander, in einem einzigen Audio. Jeder Teil
wird erzeugt, wenn die Wiedergabe ihn braucht, etwa eine Minute im Voraus: Der Ton startet schnell,
und ein Stopp erzeugt (und bezahlt) den Rest nicht. Die Übergänge zwischen den Teilen können hörbar
sein.

## Eine Auswahl vorlesen

Markiere Text im Chat oder im Dateibetrachter: Das schwebende Widget zeigt die
**Vorlesen-Schaltfläche** (das Lautsprechersymbol) neben den Schaltflächen Kommentieren und Kopieren.
Im Terminal, im Browser-Bereich und auf den Elementkarten erscheint sie nicht. Sie erscheint nur,
wenn der Sprachdienst verfügbar ist, genau wie die Lautsprecher-Schaltfläche einer Nachricht.

- Die Schaltfläche liest den markierten Text mit der aktiven Konfiguration vor. Ihr Titel lautet
  „Vorlesen“.
- Während sie vorliest, ist dieselbe Schaltfläche ein **Stopp-Symbol** (Titel „Vorlesen stoppen“) und
  bleibt sichtbar. Klicke darauf, um die Anfrage abzubrechen oder den Ton zu stoppen.
- Der Ton läuft weiter, wenn sich das Widget schließt. Stoppe ihn über die Sprachanzeige der Leiste,
  wie bei einer Nachricht.
- Ein Text, der nach dem Entfernen seiner Formatierung nichts zum Vorlesen enthält, wird abgelehnt,
  wie bei einer Nachricht.

## Den Text anpassen

Ein kleines Modell kann **einen Text fürs Hören umschreiben**, bevor er vorgelesen wird, wenn der Text
nicht einfach ist: Eine Tabelle wird zu Sätzen, eine Liste zu einer gesprochenen Aufzählung, ein
Codeblock zu einer kurzen Beschreibung; Hashes, lange Pfade und die Markdown-Syntax entfallen. Inhalt
und Sprache bleiben gleich. Ein **einfacher Text wird sofort vorgelesen**, ohne seine
Markdown-Zeichen und ohne Modell: Der Ton startet früher, und für das Umschreiben fällt nichts an.
Ein Markdown-Link wird mit seinem Text vorgelesen.

Der Test einer Konfiguration nimmt nie das direkte Vorlesen: Er läuft über das Modell, sobald das
Modell laufen kann, und zeigt so die Anpassung; sein letzter Testlauf zeigt den angepassten Text. Mit
**Aus** oder ohne verfügbares Modell wird der Text wie geschrieben vorgelesen.

Diese Texte sind zum Beispiel **nicht einfach** und laufen über das Modell:

- Tabellen, Code und codeähnlicher Text (alles mit Code-Symbolen wie `$`, `=`, `|` oder geschweiften
  Klammern, Namen in `snake_case`, `#123`) sowie HTML;
- Adressen, Pfade, Hashes und lange Zahlen;
- lange Listen: mehr als fünf Einträge, lange Einträge oder Unterlisten;
- eine Auswahl mit Zeilenumbruch oder Tabulator im Chat und jede Auswahl im Dateibetrachter.

Wenn die Regeln einen Text nicht sicher erkennen, entscheidet das Modell.

Wähle das Modell unter **Text für die Sprachausgabe anpassen**, oben in **Einstellungen →
Sprachausgabe**: **Claude Haiku 5.5**, **GPT-6 Luna**, **OpenRouter** oder **Aus**, was jeden Text
wie geschrieben vorliest. Ohne Auswahl nimmt TwiCC Claude, sonst Codex. Ein Claude- oder
Codex-Modell, dessen Anbieter deaktiviert ist, wird durch den anderen Agenten ersetzt, und deine Wahl
kehrt zurück, sobald du den Anbieter wieder aktivierst.

Claude Haiku 5.5 und GPT-6 Luna laufen über dein Claude- oder Codex-Abonnement. **OpenRouter** ruft
ein schnelles Modell bei OpenRouter auf: derzeit Qwen3.8 27B, bereitgestellt von Cerebras. Es braucht
den OpenRouter-Schlüssel, genau wie die OpenRouter-Konfigurationen. Es sendet den Text an OpenRouter
und wird über dein OpenRouter-Konto bezahlt. Ist der Schlüssel gesetzt, passt es den Text auch dann
an, wenn kein Anbieter läuft.
Ohne Schlüssel ersetzt die Standardeinstellung es, und deine Wahl kehrt zurück, sobald der Schlüssel
gesetzt und TwiCC neu gestartet ist.

Schlägt OpenRouter fehl, bevor das Vorlesen beginnt, passt der erste laufende Agent (Claude, sonst
Codex) den Text an. Kann kein Modell laufen, ist der Text zu lang oder schlägt das Modell fehl, bevor
das Vorlesen beginnt (bei OpenRouter: wenn auch dieser Agent fehlschlägt oder wenn kein Anbieter
läuft), wird der Text **wie geschrieben** vorgelesen. Schlägt das Modell später fehl, stoppt das
Vorlesen mit einem Fehler.

Der letzte Testlauf einer Konfiguration zeigt das Modell, das den Text angepasst hat, oder jedes
Modell, das fehlgeschlagen ist, samt Grund: Ein falscher Schlüssel oder ein leeres OpenRouter-Konto
zeigt sich dort. Die Kosten eines Tests sind nur die Kosten der Sprachausgabe: Die Anpassung über
OpenRouter ist darin nicht enthalten.

## Skript-Konfigurationen

TwiCC führt den **Befehl** in deiner Shell aus, als nicht interaktive Login-Shell in deinem
Home-Verzeichnis, und schreibt den Text auf dessen **Standardeingabe**. Der Befehl schreibt das Audio
auf seine **Standardausgabe** und beendet sich mit Status 0. Der Text gelangt nie in die
Befehlszeile.

| Ausgabe | Der Befehl schreibt | Der Ton startet |
|---|---|---|
| **Audio-Stream** | das Audio, während es erzeugt wird | bei den ersten Bytes |
| **Ganzes Audio** | das Audio, am Ende | wenn der Lauf endet |
| **Dateipfad** | einen absoluten Pfad, bei jedem Lauf zu einer neuen Datei | wenn der Lauf endet |

| Format | Was der Befehl liefert |
|---|---|
| **MP3** | MP3 |
| **WAV** | 16-Bit-PCM-WAV (ein fortlaufend geschriebenes WAV mit unbekannten Größen ist in Ordnung) |
| **PCM** | rohes 16-Bit-Little-Endian-PCM: gib Abtastrate und Kanäle an |

**Max. Zeichen pro Lauf**: Bleibt das Feld leer, geht der gesamte Text in einen Lauf. Andernfalls wird
der Text in Teile von höchstens dieser Größe aufgeteilt, mit je einem Lauf. Setze den Wert bei
**Ganzes Audio** oder **Dateipfad**: Der Ton startet dann nach dem ersten Teil, nicht erst nach dem
gesamten Text.

**Datei nach dem Vorlesen löschen** (nur bei Dateipfad, standardmäßig an): TwiCC löscht die Datei,
sobald es sie gelesen hat und der Lauf erfolgreich war. Es prüft nur, ob der Pfad eine reguläre Datei
ist, nicht, wo sie liegt: Stelle sicher, dass der Befehl seine eigene neue Datei zurückgibt. Ein
fehlgeschlagener Lauf behält seine Datei.

### Wenn es fehlschlägt

- Ein Befehl, der mit einem Status ungleich 0 endet, zeigt die **letzte Zeile seiner
  Fehlerausgabe**.
- Andernfalls sagt TwiCC, was nicht stimmt: eine Ausgabe, die nicht das gewählte Format hat, kein
  Audio, ein Pfad, der nicht genau ein absoluter Pfad ist …
- **Auf die Standardausgabe darf sonst nichts gedruckt werden**: Eine Nachricht aus deinem Shell-Profil
  oder eine Fortschrittszeile zerstört das Audio.
- Was `~/.bashrc` oder `~/.zshrc` setzt, fehlt meist (Aliase, Ergänzungen von `PATH`): Nutze volle
  Pfade oder ein Skript, das setzt, was es braucht.

### Gut zu wissen

- Ein Stopp beendet den gesamten Befehl, Pipelines eingeschlossen.
- Mehrere Wiedergaben können den Befehl gleichzeitig ausführen (ein Test und eine Nachricht, zwei
  Geräte).
- Ein Audio-Stream, der nach seinem ersten Audio eine Minute lang nichts sendet, wird gestoppt. Das
  Audio eines Vorlesens ist auf 512 MB begrenzt.
- TwiCC sieht nur die Befehlszeile: Teste die Konfiguration erneut, nachdem du ein Skript bearbeitet
  hast, das der Befehl ausführt (sonst spielt eine Nachricht eventuell ein bis zu 10 Minuten
  zwischengespeichertes Audio ab).

### Beispiele

Ein Testton, der den Text ignoriert (**Audio-Stream**, **WAV**):

```
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ac 1 -ar 22050 -f wav -
```

[eSpeak NG](https://github.com/espeak-ng/espeak-ng) (**Audio-Stream**,
**WAV**):

```
espeak-ng --stdin --stdout -v en
```

Dasselbe über eine Datei (**Dateipfad**, **WAV**):

```
f=$(mktemp) && espeak-ng --stdin -v en -w "$f" && echo "$f"
```

[Piper](https://github.com/rhasspy/piper) (**Audio-Stream**, **PCM**, die Abtastrate der Stimme,
oft 22050 Hz, 1 Kanal):

```
piper --model ~/voices/en_US-lessac-medium.onnx --output-raw
```

## Bekannte Einschränkungen

- Auf einem iPhone oder iPad (und in Safari für MP3) wird das Audio vor dem Abspielen vollständig
  heruntergeladen: Der Ton startet später.
- Unter Android kann ein langes Vorlesen in PCM (oder WAV) kurz nach dem Sperren des Bildschirms
  stoppen: Der Browser friert die Seite ein.
