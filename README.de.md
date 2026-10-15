# Video Cut

**Claude schneidet deine Videos, auf deinem Mac.** Du nimmst dich beim Sprechen auf, legst die Datei rein, wählst Videoart und Marke. Video Cut macht daraus ein fertiges Kurzvideo: Füllwörter und Pausen raus, Hochformat 9:16 (oder 16:9, 1:1, 4:5 je Video), animierte Untertitel, Grafiken, die deine Aussagen belegen, sauberer Ton, Musik darunter.

Video und Ton verlassen den Rechner nie. An Claude gehen nur Text (das Transkript und der lokal auf dem Mac gelesene Bildschirmtext) und kleine Standbilder an Entscheidungspunkten.

> **Stand: frühe Entwicklung (0.0.x).** Installer, Schnitt-Pipeline und die lokale App laufen; mit Ecken und Kanten ist zu rechnen. Gesamtkonzept: [`docs/BRIEF.md`](docs/BRIEF.md), Fortschritt: [`CHANGELOG.md`](CHANGELOG.md).

## Installation

```bash
git clone https://github.com/derFlok/video-cut.git
cd video-cut
./start.sh --selftest
```

Voraussetzungen: macOS 14+ auf Apple Silicon, 16 GB RAM empfohlen, rund 15 GB freier Speicher, und Claude Code mit eigenem Login oder ein API-Key (siehe [`docs/AUTH.md`](docs/AUTH.md)).

`start.sh` installiert alles nach `./.tools` (ohne Homebrew, ohne Admin-Rechte). Jeder Download ist auf eine Version festgelegt und per SHA-256 geprüft. Mehrfaches Ausführen ist unbedenklich.

Marken, Projekte und Modelle liegen in `~/VideoCut` (änderbar über `VIDEOCUT_HOME`), nie im Repository.

## Die App benutzen

```bash
./start.sh --ui            # oder nach der Installation: .tools/bin/uv run videocut ui
```

Die App läuft nur auf deinem Mac (`127.0.0.1`) und öffnet sich im Browser über einen
Einmal-Link; Terminalfenster schließen beendet sie. Deutsch ist voreingestellt, Englisch
über den Umschalter oben.

1. **Neues Video:** Videoart wählen, die Kamera-Aufnahme auf die Seite ziehen (optional eine
   gleichzeitig laufende Bildschirmaufnahme oder Screenshots), Marke, Ausgabeformat und
   Schnittstil wählen, eine Zeile Anweisung an Claude schreiben. Mit dem Hochladen beginnt die
   Vorbereitung.
2. **Fortschritt verfolgen:** die Schritte, ein Live-Protokoll, Laufzeit und bisherige
   Claude-Kosten.
3. **Plan lesen:** Hook, Kernaussage, was drin bleibt (mit Zeiten und Begründung), die
   Bildausschnitte mit Standbildern, Einblendungen und Risiken. Änderungen in eigenen Worten
   anfragen, z. B. *„Den Schluss kürzer, der letzte Satz reicht“* oder *„Die Frage zu den
   Anrufen weglassen, dafür das Beispiel länger“*. Den Plantext kannst du auch direkt
   bearbeiten.
4. **Persönliche Daten:** Hat der Plan Namen, E-Mail-Adressen oder Nummern im Bild gefunden,
   entscheidest du je Fund, was verdeckt wird (voreingestellt: alles) und wie: unscharf,
   abgeschnitten (eine Browserleiste oder Seitenleiste am Bildschirmrand) oder als volle
   Fläche; „automatisch“ schneidet durchgehende Leisten ab und macht den Rest unscharf.
   Dann **freigeben**:
   erst jetzt schneidet Claude, ergänzt Untertitel und Einblendungen, rendert und prüft das
   eigene Ergebnis.
5. **Ergebnis:** Reel abspielen, die automatische Prüfung in Klartext lesen und an der
   aktuellen Stelle des Players Korrekturen geben, z. B. *„Hier weniger rauszoomen“*,
   *„Der Untertitel bei 0:12 hat ein falsches Wort“* oder *„Die Frage zu den Anrufen
   weglassen“*. Nur die betroffenen Stellen ändern sich; jede Version bleibt gelistet (die
   letzten fünf lassen sich wieder abspielen).
6. **Herunterladen:** MP4 und Cover.

Auf der Videoseite kannst du ein Video umbenennen und **duplizieren** (Menü „⋯“), z. B. um
einen anderen Schnittstil oder ein anderes Format auszuprobieren, ohne das erste Ergebnis zu
verlieren; die Kopie braucht eine eigene Freigabe, außer du übernimmst die Freigabe.

**Marken** (oben in der Navigation): Vorschaubögen je Format, Farben, Schriften,
Untertiteltempo und Anrede, Logo- und Schrift-Upload; neue Marken starten mit dem neutralen
Beispiel. Die App prüft eine Marke genauso wie `videocut tool brand check`.

## Benutzung (Kommandozeile)

```bash
.venv/bin/videocut run ~/Movies/aufnahme.mov --type thought --brand meine-marke --brief "eine These, starker erster Satz"
```

1. **Vorbereitung** (lokal, ohne Claude): Projektordner in `~/VideoCut/jobs/<id>`,
   Rauschunterdrückung, Transkript mit Wortzeiten, Füllwort- und Pausenkandidaten, Kontaktbögen.
2. **Plan:** Claude liest das Transkript und wenige Standbilder und schreibt `work/project.md`:
   Hook, Kernaussage, Ziel-Länge, was bleibt und was raus fliegt, Betonungen.
3. **Du gibst frei** – im Terminal (vorher `project.md` gern anpassen). Vorher wird nichts
   geschnitten. Später freigeben: `videocut tool job approve <id>`, dann `videocut run <id>`.
4. **Schnitt:** Claude schreibt die Schnittliste, rendert eine Vorschau, prüft jeden Schnitt
   (Knackser, Bildsprünge, Lautheit), korrigiert bis zu drei Runden und rendert
   `out/reel.mp4` (im Format des Auftrags, z. B. 1080×1920; −14 LUFS). `work/cut_report.md` nennt Änderungen und offene Fragen.
5. **Untertitel und Einblendungen** (mit `--brand`): Claude ergänzt Untertitel und wenige
   Einblendungen (Schlagworte, Zahlen, Fragen, Screenshots) im Look deiner Marke, nur mit dem,
   was du sagst. Nichts liegt im Gesicht oder in den UI-Zonen der App, das wird automatisch
   geprüft. Danach rendert Claude das fertige Reel. `work/overlay_report.md` listet die
   Einblendungen.

Korrekturen: `videocut run <id> --plan-feedback "Schluss kürzer"` überarbeitet den Plan vor der
Freigabe (danach neu freigeben); `videocut run <id> --feedback "hier weniger zoomen" --at 12`
korrigiert das fertige Reel an Sekunde 12 ohne neue Freigabe (das vorherige Reel bleibt in
`out/history/`, jede Korrektur steht in `work/revisions.jsonl`).

Optionen: `--approve` (ohne Nachfrage), `--replan`, `--recut`, `--brand <id>`, `--screen DATEI`
(gleichzeitig aufgenommene Bildschirmaufnahme, für einen neuen Auftrag), `--asset DATEI...`
(Screenshots für einen neuen Auftrag; als letzte Option angeben), `--demo-data` (gefundene
persönliche Daten sind Demodaten), `--format 9:16|16:9|1:1|4:5` (Ausgabeformat; bei einem
bestehenden Auftrag ersetzt es das Format und schneidet neu), `--cut-style calm|standard|tight`
(siehe unten), `--target 90|5m|2:30|auto` (Ziellänge, siehe unten), `--max-turns N`. Zeit, Turns,
Kosten und Tokens jedes Claude-Laufs stehen in `work/runs.jsonl`.

**Ausgabeformat** (je Video): `9:16` 1080×1920 (Reels, Shorts, Stories), `16:9` 1920×1080
(YouTube, Website, Präsentationen), `1:1` 1080×1080 und `4:5` 1080×1350 (Feed-Beiträge).
Standard: `16:9`, wenn der Auftrag eine Bildschirmaufnahme oder Screenshots hat
(Produktdemo), sonst `9:16`. Später ändern mit `videocut tool job format <id> 16:9`;
`videocut tool job show <id>` zeigt es an.

**Schnittstil** (je Video): wie viel von deinem natürlichen Sprechrhythmus bleibt.
`calm` (ruhig) behält ganze Sätze und Pausen bis zu einer Sekunde, mit einer weichen
Tonüberblendung von 120 ms an jedem Schnitt – für Demos und Erklärungen, bei denen man
nebenbei den Bildschirm liest. `standard` entfernt Füllwörter und Versprecher, lässt Sätze
sonst ganz – für Videos, in denen du in die Kamera sprichst. `tight` (straff) entfernt jede
Pause und jedes Füllwort – für schnelle Reels. Standard: `calm` mit Bildschirmaufnahme oder
Screenshots, sonst `standard`. Ändern mit `videocut tool job cut-style <id> standard` oder in
der App (Plan- oder Ergebnisseite); der freigegebene Plan und deine Entscheidung zu
persönlichen Daten bleiben, die App bietet an, neu zu schneiden. Jeder Schnitt wird auf
eine echte Pause im Ton gelegt, und die Prüfung schlägt an, wenn ein Wort abgeschnitten ist.

**Ziellänge** (je Video): `auto` ergibt Reels von 30–60 s (30–45 s für einen Gedanken) und
lässt eine Produktdemo so lang laufen, wie der Inhalt braucht – straff, aber vollständig.
Mit einer Länge (z. B. `5m`) zielt der Plan auf ± 15 % davon; er streckt nie – reicht das
Material nicht, sagt er das und schlägt die natürliche Länge vor. Wählbar beim Anlegen
oder später mit `videocut tool job target <id> 5m` bzw. auf der Planseite; eine Änderung
braucht einen neuen Plan und eine neue Freigabe.

## Produktdemo aufnehmen

```bash
.venv/bin/videocut run ~/Movies/ich.mov --screen ~/Movies/bildschirm.mov --brand meine-marke
.venv/bin/videocut run ~/Movies/ich.mov --brand meine-marke --asset ~/Desktop/shot-1.png ~/Desktop/shot-2.png
```

1. **Bildschirm:** Cmd+Shift+5 drücken, „Gesamten Bildschirm aufnehmen" (oder einen Teil)
   wählen und unter *Optionen* dein **Mikrofon** wählen und **Mausklicks anzeigen**
   einschalten. Über die Mikrofonspur legt Video Cut Bildschirm und Kamera übereinander.
   **Das Mikrofon in beiden Aufnahmen einschalten** (Kamera und Bildschirm): ohne Ton in
   einer davon lassen sich die beiden nicht übereinanderlegen.
2. **Du selbst:** die Kameraaufnahme von dir **gleichzeitig** starten (Handy oder Webcam),
   dann die Bildschirmaufnahme. Optional: zu Beginn einmal sichtbar in die Hände klatschen;
   das hilft bei lauter Umgebung.
3. Die Demo wie gewohnt zeigen und erklären; am Ende beide Aufnahmen stoppen.

   Tipp für eine 16:9-Demo (hier der Standard): den Mac-Bildschirm in voller (nativer)
   Auflösung aufnehmen – der Bildschirm füllt dann das Video und bleibt scharf, gezoomt wird
   nur bei kleiner Schrift. Dich selbst mit einer eigenen Kamera aufnehmen (Handy oder
   Webcam); ein Kamerabild von dir ergibt eine viel bessere runde Bild-im-Bild-Blase als eine
   kleine Videocall-Kachel.
4. Den ersten Befehl oben starten. Die Vorbereitung synchronisiert beide Aufnahmen
   (`work/sync.json`) und findet Klicks und Änderungen auf dem Bildschirm. Der Plan sagt,
   wo du zu sehen bist, wo der Bildschirm oder beides (in 16:9: der Bildschirm füllt das Bild
   und du bist als runde Blase in einer Ecke, oder der Bildschirm neben dir; in 9:16: du
   klein unter dem Bildschirm, oder Bildschirm oben und du unten), wohin gezoomt wird und welche Stellen persönliche Daten
   zeigen könnten (E-Mail-Adressen, Telefonnummern, Namen). **Demodaten?** Du wirst bei der
   Freigabe gefragt: alles Demo, alles abdecken oder Nummern wählen (oder `--demo-data`).
   Ohne Antwort wird alles Gelistete im Verdeckungsstil des Videos verdeckt
   (`--redact-style auto|blur|crop|block`, voreingestellt auto: Browserleisten oben oder unten
   werden abgeschnitten, der Rest unscharf). Wo der Text steht, misst
   die Texterkennung des Macs auf der Aufnahme (lokal), und die Prüfung liest das fertige
   Reel, um sicherzugehen, dass er abgedeckt ist.
5. Ist die Synchronisierung unsicher, hält der Lauf an und erklärt, wie du sie von Hand
   setzt: `videocut tool sync --job <id> --offset <Sekunden>` (Bildschirmzeit minus
   Kamerazeit eines Moments, den man in beiden sieht oder hört), dann `videocut run <id>`.

**Nur Screenshots:** Screenshots machen (Cmd+Shift+4), dich beim Erklären aufnehmen und den
zweiten Befehl starten. Claude ordnet jeden Screenshot dem Satz zu, der ihn erklärt, und
zeigt ihn mit Zoom auf die genannte Stelle; persönliche Daten darin werden abgedeckt.

## Folien und Animationen

Claude kann zeigen, was du sagst: Schritte, eine Aufzählung, eine große Zahl, einen
Vergleich, eine Zeitleiste, ein Balkendiagramm, ein Zitat oder den Handlungsaufruf, im
Look deiner Marke (es wird kein Code erzeugt; jede Folie ist eine Vorlage, gefüllt mit
deinen gesagten Worten). Oder du bringst eigene Folien mit (PDF-Export oder Bilder), und
Claude setzt jede Seite dorthin, wo du über sie sprichst. In der App: **„Folien &
Animationen"** unter *Neues Video* – keine, eigene hochladen, Claude erstellt sie oder
beides, dazu „Was soll gezeigt werden?". Der Plan zeigt jede Folie mit Vorschau, der
Stelle, zu der sie gehört, und ihrem Layout (Folie groß mit dir im Kreis, nebeneinander
oder nur die Folie); vor der Freigabe kannst du eine entfernen oder eine Änderung wünschen.

```bash
.venv/bin/videocut run ~/Movies/ich.mov --brand meine-marke --slides-mode auto \
  --slides-wish "die drei Schritte als Ablauf"
.venv/bin/videocut tool job slides-import <id> ~/Desktop/folien.pdf   # eigene Seiten
.venv/bin/videocut tool job slides <id> --mode both                   # dann: videocut run <id>
```

PDF-Seiten werden auf diesem Mac in Bilder umgewandelt (mit seiner eigenen PDF-Technik);
nichts wird hochgeladen. Claude schreibt keine Zahl und keine Aussage auf eine Folie, die
du nicht gesagt oder gewünscht hast. Wer die Folien nach dem Plan ändert, braucht einen
neuen Plan und eine neue Freigabe.

## Marken

Video Cut bringt kein eigenes Branding mit. Du lädst deins hoch (Logo, Farben, Schriften, optional Brand-Guide und Beispielposts), und jedes Video passt sich daran an. Mehrere Marken nebeneinander sind möglich, pro Video wählst du eine.

Eine Marke ist ein Ordner `~/VideoCut/brands/<id>/` mit `brand.json` und ihren Schrift- und
Logodateien (Vorlage: `brands/_example`).

```bash
.venv/bin/videocut tool brand check meine-marke   # prüfen; Kontrasttabelle (Untertitel ≥ WCAG AA)
.venv/bin/videocut tool brand sheet meine-marke   # Vorschau-Bogen aller Szenen → brands/meine-marke/preview.jpg
```

### Wörterbuch

Schreibt die Transkription einen Namen immer wieder falsch (deine Firma, ein Produkt), trag
ihn im **Wörterbuch** der Marke in der App ein (oder nur für ein Video: „Begriffe für dieses
Video“): die richtige Schreibweise und die Falschschreibungen, die dir begegnet sind. Whisper
bekommt die richtige Schreibweise vorgegeben, jede Falschschreibung wird im Transkript und in
den Untertiteln korrigiert, und Claude schreibt den Namen in Titeln und Karten so. Bei einem
fertigen Video korrigiert „Auf das Transkript anwenden“ ohne neue Transkription und bietet
„Untertitel neu setzen“ an. Auf der Videoseite stehen außerdem Vorschläge („Meintest du …?“).

```bash
.venv/bin/videocut tool brand vocab meine-marke add Zentrovia Zentrowia "Sentro Via"
.venv/bin/videocut tool job vocab <job> add "Lumo Cloud" Lumocloud   # nur dieses Video
.venv/bin/videocut tool vocab apply <job>      # bestehendes Transkript korrigieren
.venv/bin/videocut tool vocab suggest <job>    # mögliche Falschschreibungen (nie automatisch)
.venv/bin/videocut run aufnahme.mov --vocab "Zentrovia=Zentrowia,Sentro Via"
```

## Lizenz

Apache-2.0. Unabhängiges Projekt, nicht mit Anthropic verbunden.
