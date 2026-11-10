Die Modellauswahl ist eine **Matrix**: eine Zeile pro Modell, eine Spalte pro Reasoning-Aufwand. Jede auswählbare Zelle zeigt einen **Score von 0 bis 100**:
wie gut dieses Modell mit diesem Aufwand zu der Aufgabe passt, die du mit den
Reglern unter der Matrix beschreibst. Höher ist besser. Ein dezentes **?** bedeutet, dass es
für dieses Paar keine brauchbaren Daten gibt.

Die Matrix hat einige visuelle Hinweise:

- der **Hintergrund** der Zelle wird mit steigendem Score farbiger;
- ein **Häkchen** markiert die aktuell ausgewählte Zelle, ein kleiner **Punkt** die
  Standardzelle;
- ein **Ring** hebt den besten Score jedes Anbieters hervor: durchgezogen für deinen
  Standardanbieter, gestrichelt für die anderen, wenn mehrere Anbieter
  angezeigt werden.

## Woher die Daten stammen

Die Scores basieren auf den Auswertungen von
**[Artificial Analysis](https://artificialanalysis.ai/)**, einer unabhängigen
Benchmarking-Site. Sie misst jedes Modell, bei jedem Reasoning-Aufwand, in
vielen Auswertungen, mit den **Kosten** und der **Zeit** jeder Aufgabe.

TwiCC liefert einen Snapshot dieser Ergebnisse mit, erstellt am **2026-11-10**, auf Basis von
Version 5.0.1 des Intelligence Index. Er wird nicht automatisch aktualisiert.

## Die Regler

- **Aufgabentyp**: welche Art von Arbeit du vorhast (siehe unten).
  *Allgemein* nutzt den gesamten Intelligence Index von Artificial Analysis.
- **Aufgabenschwierigkeit**: wie leistungsfähig das Modell sein muss. Schiebe den Regler nach oben für eine
  schwere Aufgabe, nach unten für eine leichte.
- **Bevorzugen**: **Kosten**, um günstigere Paare zu bevorzugen, **Geschwindigkeit**, um schnellere
  zu bevorzugen.
- **Beste automatisch wählen**: wählt die Zelle mit dem besten Score für dich aus, sobald sich ein
  Regler ändert, genau so, als hättest du sie angeklickt. Werden mehrere Anbieter
  angezeigt, beschränkt **Nur Standardanbieter** diese Auswahl auf deinen Standardanbieter.
- **Beste automatisch wählen** und **Ältere Modelle anzeigen** werden jedes Mal deaktiviert, wenn
  die Auswahl, der Einstellungsbereich oder ein Voreinstellungsformular geöffnet oder geschlossen wird.

## Was der Score bedeutet

Die Schwierigkeit legt ein **Zielniveau** auf der Fähigkeitsskala des Aufgabentyps fest.
Ein Paar **auf oder über** dem Ziel erledigt die Aufgabe: Unter diesen gewinnt das günstigere
(oder schnellere). Ein Paar knapp **unter** dem Ziel verliert einige
Punkte. Ein Paar weit darunter verliert so viele, dass ein niedriger Preis es nicht retten kann.

- **100** ist das beste Paar für diese Aufgabe.
- **50** heißt „eine Verdopplung schlechter“: doppelte Kosten (oder Zeit) im Vergleich zum Besten
  oder so weit unter dem Ziel, dass es genauso viel zählt.
- Weiter entfernte Paare behalten **kleine Scores über null**. Mehrere davon
  können dieselbe Zahl zeigen: Der **Ring** markiert trotzdem das beste Paar jedes Anbieters,
  sodass du das beste Paar eines einzelnen Anbieters wählen kannst.
- Die Scores vergleichen **alle aktivierten Anbieter** gemeinsam, sodass du
  zwischen Claude Code und Codex wählen kannst. Deaktivierte Anbieter, eingestellte oder deaktivierte
  Modelle und Aufwandsstufen, die ein Modell nicht unterstützt, zählen nie.
- Ältere Modelle zählen nur, solange **Ältere Modelle anzeigen** aktiv ist.
  Sonst vergleichen die Scores die neuesten Modelle, also liegt die 100 immer
  auf einer sichtbaren Zelle. Ein bereits ausgewähltes älteres Modell bleibt
  sichtbar und wird gegen die neuesten Modelle bewertet (höchstens 100),
  bekommt aber nie den Ring und nie **Beste automatisch wählen**.
- Version 5.0.1 des Intelligence Index misst nur wenige ältere Modelle, oft
  nur mit ihrem höchsten Aufwand. Ihre anderen Zellen zeigen **?**.

## Aufgabentypen

| Aufgabentyp | Basiert auf |
|---|---|
| Allgemein | Intelligence Index (Gesamtscore von Artificial Analysis); Kosten und Zeit aus Terminal-Bench 4.0 |
| Coding & Terminal | Terminal-Bench 4.0 (agentisches Coding in einem Terminal), FrontierCode 2.0 (Coding) |
| Büro & Wissensarbeit | AA-Briefcase (Ergebnisse von Wissensarbeit), GDP.pdf (Dokument-Reasoning) |
| Automatisierung & Tools | AutomationBench-AA (SaaS-Workflows mit Tools) |
| Wissenschaft & Schlussfolgern | HLE Diamond (schwieriges Reasoning), CritPt v2 Hard (Physik), Terminal-Bench-Science (wissenschaftliche Arbeit in einem Terminal) |
| Faktenwissen | AA-Omniscience (Wissen und Halluzination) |

Bei einem Typ, der auf mehreren Auswertungen beruht, ist die Fähigkeit deren Durchschnitt
(jeweils auf einer Skala von 0 bis 100), ebenso Kosten und Zeit.

## Unter der Haube (für Neugierige)

- Der Regler bildet **0** auf das schwächste Paar und **100** auf das stärkste
  für den gewählten Aufgabentyp ab, auf einer Kurve, die zuerst schnell steigt: Die
  Reglermitte verlangt bereits ein leistungsfähiges Modell.
- **Keine Strafe über dem Ziel**: Ein Paar, das es übertrifft, zahlt nur mit
  seinen Kosten (oder seiner Zeit).
- **2 Punkte des Intelligence Index** unter dem Ziel wiegen so viel wie
  der doppelte Preis. Andere Aufgabentypen skalieren das mit ihrem eigenen Bereich.
  Die Strafe wächst mit der **4. Potenz** der Abweichung: 4 Punkte darunter
  wiegen so viel wie 2¹⁶-fach höhere Kosten.
- Der Score fällt von 100 auf 50 in der ersten Verdopplung, danach immer
  langsamer, sodass er nie auf 0 einbricht.
- Kosten und Zeit werden auf einer **logarithmischen** Skala verglichen: Es zählt
  das Verhältnis (von 1 $ auf 2 $ zählt wie von 10 $ auf 20 $).
- Die Zeit ist die Zeit pro Aufgabe von Artificial Analysis: die Zeit für das Erzeugen
  der Antwort, ohne Startverzögerungen.
