TwiCC benennt deine Sitzungen automatisch. Um den Stil zu steuern, öffne
**Einstellungen**, dann **Titelvorschlag**. Wähle, ob jede Sitzung ihren Anbieter,
Claude Haiku oder GPT-6 Luna verwendet.

Bearbeite den Prompt, um eine Sprache, eine Längenbegrenzung, ein Präfix oder
einen anderen Stil anzufordern.

Verwende **{text}** im Prompt als Platzhalter für den Sitzungsinhalt.

**Automatische Titel** steuert sowohl den ersten Titel als auch spätere
Aktualisierungen. Die Titelgenerierung muss ebenfalls aktiviert sein. Echte
Sitzungen erhalten ihre Titel vom Backend, auch wenn kein Sitzungs-Tab geöffnet
ist. Änderungen erscheinen unauffällig in der Seitenleiste und im Kopf.

Die erste Prüfung braucht eine relevante Nutzernachricht. Spätere Prüfungen
brauchen mindestens **6 neue Nachrichten und 15 Minuten** seit dem letzten
Versuch. Ein manuelles Stoppen oder Archivieren löst eine abschließende Prüfung
mit **3 neuen Nachrichten** seit der letzten erfolgreichen Prüfung aus,
unabhängig von der Zeit. Ein normales Beenden der CLI löst sie nicht aus.
Reine Slash-Befehle zählen nicht; Befehle mit Argumenten können zählen.

TwiCC behält den aktuellen Titel, außer die Hauptthemen der Unterhaltung brauchen
eine bessere Abdeckung. Eine fehlgeschlagene Prüfung behält die Nachrichten für
einen späteren Versuch. Prüfungen folgen der Sitzungsaktivität; verstrichene Zeit
allein löst keine Prüfung aus.

Ein laufendes Hybrid-Terminal kann seinen ersten Titel erhalten. Automatische
Aktualisierungen warten, bis das Terminal stoppt oder die Sitzung außerhalb des
Terminals fortgesetzt wird. Eine gestoppte Hybrid-Sitzung kann normal
aktualisiert werden.

Flüchtige Sitzungen behalten Frontend-Vorschläge, weil sie noch keine
gespeicherte Sitzung haben. Bestehende Titel aus der Zeit vor dieser Funktion
bleiben eingefroren. Ausgeblendete, veraltete und Subagent-Sitzungen erhalten
keine automatischen Titel. Geeignete, von Agenten erstellte Sitzungen können sie
erhalten, wenn kein Titel angegeben wurde.
