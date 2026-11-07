Die **Externe Adresse** ist die Adresse, unter der du *diese* TwiCC-Instanz von deinen
Geräten aus erreichst. TwiCC nutzt sie auch, um Links zurück zu deinen Sitzungen zu erzeugen. Eine externe
Benachrichtigung kann dann die richtige Unterhaltung öffnen statt einer toten `localhost`-Adresse.

### Wofür sie gedacht ist

- Öffne eine Sitzung von deinem **Smartphone oder einem anderen Computer** aus, während TwiCC
  weiter auf deinem Rechner zu Hause läuft.
- Sie wird außerdem in **externe Benachrichtigungen** (Apprise) eingebaut: Ein Push-Alarm
  kann dann direkt zur zugehörigen Sitzung verlinken.

Lässt du sie leer, lässt TwiCC diese Links einfach weg. Sonst geht nichts kaputt.

### Warum du einen Tunnel brauchst

TwiCC läuft als lokaler Prozess, daher hat `http://localhost:…` nur auf dem Rechner eine Bedeutung,
auf dem es läuft. Um es von anderswo zu erreichen, machst du es über einen
**Tunnel** verfügbar: einen Dienst, der deiner lokalen Instanz eine stabile öffentliche Adresse gibt und den Datenverkehr
dorthin zurückleitet. Genau diese öffentliche Adresse trägst du als Externe Adresse ein. Einige beliebte Optionen:

- **Cloudflare Tunnel** (`cloudflared`): ein benannter Tunnel mit stabilem Hostnamen.
- **Tailscale**: ein privates Mesh-VPN zwischen deinen eigenen Geräten, optional mit
  öffentlicher Freigabe (Funnel).
- **ngrok**: schnelle öffentliche URLs für einen lokalen Port.

### Schütze deinen Tunnel

Ein Tunnel kann deine Instanz aus dem offenen Internet erreichbar machen, nimm das also
ernst. Jeder Anbieter bietet Möglichkeiten, ihn abzusichern. Nutze sie:

- **Cloudflare Tunnel** → schalte **Cloudflare Access** davor (E-Mail-Einmal-PIN, Google/GitHub SSO), füge IP-Regeln oder WAF hinzu.
- **Tailscale** → halte ihn in deinem **privaten Tailnet**, sodass nur deine eigenen Geräte
  ihn erreichen (vermeide das öffentliche Funnel, außer du brauchst es wirklich). Verschärfe das mit Geräte-ACLs.
- **ngrok** → aktiviere die integrierte **Basic Auth**, **OAuth** (Google, GitHub…)
  oder IP-Allowlists.

### Das eigene Passwort von TwiCC hat Vorrang

Am wichtigsten: **TwiCC lehnt jede nicht lokale Verbindung ab, solange du kein
Passwort gesetzt hast.** Eine Externe Adresse funktioniert also erst, nachdem du
`twicc password set` ausgeführt hast. Dieses Passwort ist dein primärer Schutz.

(Der Schutz des Tunnel-Anbieters kommt *zusätzlich* dazu, als zweite Schicht neben dem
Passwort. Er bleibt wichtig: Er hält unerwünschten Datenverkehr ganz von TwiCC fern, noch bevor er
den Anmeldebildschirm erreicht.)
