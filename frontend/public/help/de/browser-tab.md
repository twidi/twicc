Der Browser-Tab bettet eine Seite deiner Wahl direkt neben der Unterhaltung ein, typischerweise den Dev-Server
des Projekts, an dem du arbeitest. Die Seite läuft genau wie in einem normalen Browser-Tab: direktes Netzwerk, Live-Reload,
Service Worker. TwiCC leitet sie weder über einen Proxy noch isoliert es sie in einer Sandbox.

**Damit ein Agent deinen Dev-Server einrichtet,** verweise ihn auf diese Seite:
<https://github.com/twidi/twicc/blob/main/frontend/public/help/browser-tab.md>.
Sie enthält alles Nötige: Framing, Cookies und das Companion-Skript.
Der Agent braucht außerdem die Adresse, unter der du TwiCC erreichst: Die Einrichtungshinweise im
Tab bieten **Anweisungen zur Nachricht hinzufügen** und **Anweisungen für einen Agenten kopieren**,
einen fertigen Prompt mit dieser Seite, dieser Adresse und dem Companion-Snippet.

### Eine Seite öffnen

Gib eine beliebige URL in die Adressleiste ein. Die Schaltfläche **Startseite** springt zur gespeicherten
Standard-URL. Über das **Lesezeichen**-Menü speicherst du die aktuelle URL als Standard für das Projekt
(oder für einen seiner Arbeitsbereiche). Der Tab öffnet sich dann sofort auf dieser Seite. Die URL, auf der du zuletzt warst,
wird außerdem pro Sitzung gespeichert.

### Grenzen des Einbettens

Manche Seiten lassen sich nicht einbetten:

- Seiten, die das Einbetten in Frames verweigern (die meisten öffentlichen Seiten), zeigen eine Warnung.
  Nutze dann stattdessen **In neuem Browser-Tab öffnen**.
- Wird TwiCC selbst über `https` ausgeliefert, blockiert der Browser einfache `http://`-Seiten
  (Mixed Content).

Deine lokalen Dev-Server sind der ideale Anwendungsfall; dafür ist der Tab gedacht.

### Anmeldungen und Speicher

Ob die eingebettete Seite dich angemeldet lässt, hängt davon ab, wo dein Dev-Server im Verhältnis zu TwiCC liegt.

**Gleicher Host**: TwiCC und deine App laufen beide auf `localhost`, mit beliebigen Ports. Du musst nichts tun:
Die Seite nutzt dieselben Cookies und denselben Speicher wie in einem normalen Tab, deine dortige Anmeldung
gilt also bereits. Ports spielen hier keine Rolle: `localhost:5173` und `localhost:3000` zählen als dieselbe Site.

**Unterschiedliche Domains**: z. B. TwiCC über einen Tunnel und deine App über einen anderen. Der Browser isoliert die
eingebettete Seite, du startest also abgemeldet und brauchst ein paar **nur für die Entwicklung** gedachte Einstellungen:

- **Framing erlauben**: Viele Frameworks verbieten das Einbetten standardmäßig über
  `X-Frame-Options` / `frame-ancestors` (Django, Rails…). Lockere das in der Entwicklung.
- **Cookies**: Setze dein Sitzungs-Cookie auf `SameSite=None; Secure`. `Secure`
  gilt über `https` und `http://localhost`, aber nicht für eine IP im LAN über reines http.
- Melde dich dann **einmal im Tab an**. Die eingebettete Seite bekommt einen eigenen,
  privaten Cookie- und Speicherbereich, der über Neuladen hinweg erhalten bleibt. `localStorage` und
  `IndexedDB` brauchen keine Einrichtung: gleiche Isolation, sie funktionieren einfach.

Hinterlege diese Einstellungen hinter einem Dev-Flag: `SameSite=None` oder offenes Framing willst du nicht in der Produktion.

### Das Companion-Skript

Eine eingebettete Seite ist für TwiCC eine Blackbox: Links, denen du darin folgst, sind unsichtbar, sodass
Zurück / Vorwärts nur URLs verfolgen, die du in der Symbolleiste eingibst. TwiCC liefert ein
**Companion-Skript**, das du deiner Dev-Seite hinzufügst. Es hebt diese Einschränkung auf und schaltet die
umfangreicheren Funktionen frei:

- echtes Navigations-Tracking: Zurück / Vorwärts / Neu laden steuern den eigenen Verlauf der Seite,
  und Navigation innerhalb der Seite (SPA) aktualisiert die Adressleiste;
- **Elementauswahl**: Statt „den dritten Button im Header“ zu beschreiben und zu hoffen, dass der Agent ihn findet,
  schaltest du den Modus mit der Schaltfläche **Mauszeiger** in der Symbolleiste ein, klickst das Element direkt auf der Seite an
  und tippst dann auf die Schaltfläche **Kommentar**. Es öffnet sich ein Widget, in dem du eine optionale
  Notiz hinzufügen (und einen Screenshot des Elements einbeziehen) kannst, bevor du das Ganze ins Eingabefeld
  des Chats einfügst: eine präzise, codenahe Beschreibung des
  Elements (Tag, IDs, Klassen: Dinge, die der Agent in deinem Quellcode findet)
  samt deinem Kommentar. Ideal für „mach das größer“ oder „diese Beschriftung ist falsch“;
- **Seitenfehler**: Nicht abgefangene Ausnahmen und `console.error`-Aufrufe werden
  gesammelt, in der Symbolleiste gezählt und sind mit einem Klick in deiner Nachricht.

Das Snippet ist ein einzelnes `<script>`-Tag, das auf
`/_twicc/browser-companion.js` auf deinem TwiCC-Host zeigt:

```html
<script src="https://your-twicc-host/_twicc/browser-companion.js" defer></script>
```

Füge es im `<html>` deiner Seite **so früh wie möglich im Head** ein, idealerweise als allererstes Tag im `<head>`,
vor jedem anderen Skript. Je früher es lädt, desto früher werden Navigation und Fehler erfasst (es nutzt `defer`
und blockiert deine Seite daher nie). Für die Seite selbst ist es unsichtbar und tut nichts, wenn
die Seite außerhalb von TwiCC läuft. Du kannst es also bedenkenlos in deinem Dev-Template lassen.

Du musst die URL nicht von Hand eintippen: Hat die eingebettete Seite kein Companion, bietet ein Banner im Tab
(und die Schaltfläche **Stecker**) genau dieses Snippet an, bereits mit dem richtigen Host ausgefüllt und
kopierbereit. Greifst du von einem anderen Rechner auf TwiCC zu, konfiguriere die
[Externe Adresse](help/external-url), damit das kopierte Snippet auf eine Adresse zeigt,
die dein Browser erreichen kann.

Die Schaltfläche **Stecker** zeigt den Companion-Status: in der Markenfarbe, wenn verbunden,
rot, wenn das Skript fehlt, abgedunkelt während der Erkennung.
Ein Klick darauf öffnet diese Hilfeseite.

### Responsiver Modus

Die Schaltfläche **Smartphone** gibt der Seite einen Viewport in exakter Gerätegröße: Wähle eine
Voreinstellung (oder gib Breite × Höhe ein), tausche die beiden Werte oder ziehe an den Griffen rund um den Rahmen.
Die Seite wird genau so umgebrochen wie in einem Fenster dieser Größe.
Kein Companion nötig.

### Alles andere

- **Vollbild** erweitert den ganzen Tab, einschließlich Symbolleiste.
- **In neuem Browser-Tab öffnen** übergibt die aktuelle URL an deinen echten Browser.
- Fehler, Auswahl und Kommentare landen immer in der Nachricht, die du gerade verfasst.
  Ohne dich wird nichts an den Agenten gesendet.
