Mit der **Lautsprecher-Schaltfläche neben Zeitachse** liest TwiCC neue Assistenten-Nachrichten
automatisch vor. Dafür ist eine verfügbare Sprachausgabe-Konfiguration erforderlich. Die gedrückte
Schaltfläche bedeutet, dass diese Zeitachse auf neue Nachrichten lauscht.

**Nur Nachrichten am Zugende vorlesen** ist standardmäßig eingeschaltet. Der Schalter erscheint unter
der Überschrift, solange diese Zeitachse lauscht, auch bei pausiertem Lauschen. Die Einstellung bleibt
nach dem Neuladen und erneutem Aktivieren erhalten. TwiCC liest die genaue letzte Nachricht nach einem
offiziellen Zugende vor. Sichtbare Fortschrittsmeldungen können stumm bleiben. Das Zugende muss strikt
nach dem Start des Lauschens liegen. Schalte die Option aus, um auch Zwischennachrichten vorzulesen.
Manuelles Vorlesen bleibt in beiden Modi verfügbar.

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
- Bei ausgeschalteter Option müssen Nachrichten ein natives Nachrichtendatum haben, das **strikt nach dem Klick auf die
  Aktivierung** liegt. Der vorhandene Verlauf wird nicht abgespielt, wenn du das Lauschen aktivierst.
- Bei ausgeschalteter Option bleiben Nachzügler bis zu **fünf Sekunden** hinter der neuesten Nachricht zulässig, deren Vorlesen
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

## Wo die Wiedergabe weiterläuft

Was beim Verlassen der Seite passiert, hängt davon ab, wo du TwiCC nutzt.

- **Android-App**: Das Lauschen läuft bei ausgeschaltetem Bildschirm und beim Wechsel zu
  einer anderen App weiter. Eine Benachrichtigung zeigt den Zustand und bietet **Überspringen**
  und **Alles stoppen**. Öffne TwiCC, bevor du das Lauschen aktivierst. Beim Aktivieren kann
  Android dich bitten, Benachrichtigungen zu erlauben, die die Benachrichtigungs-Schaltflächen
  brauchen, und einen Akku-Hinweis zeigen. Uneingeschränkte Akkunutzung ist optional. Sie macht
  das Lauschen im Hintergrund zuverlässiger. Ändere beides später in den Android-Einstellungen →
  Apps → TwiCC.
- **Web auf Smartphone oder Tablet**: Lass TwiCC im Vordergrund und den Bildschirm an. Der
  Browser kann die Seite einfrieren, wenn du die App wechselst oder den Bildschirm sperrst. Das
  Audio stoppt dann.
- **Web auf dem Computer**: Lass den Tab offen. Er darf im Hintergrund bleiben, hinter anderen
  Tabs oder Fenstern. Der Browser kann einen verborgenen Tab verlangsamen, aber das Lauschen
  läuft normalerweise weiter.

Zum Einrichten der Stimme siehe [Sprachausgabe](help/text-to-speech).
