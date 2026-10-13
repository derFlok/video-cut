# Video Cut

**Claude schneidet deine Videos, auf deinem Mac.** Du nimmst dich beim Sprechen auf, legst die Datei rein, wählst Videoart und Marke. Video Cut macht daraus ein fertiges Kurzvideo: Füllwörter und Pausen raus, Hochformat 9:16 (oder 16:9, 1:1, 4:5 je Video), animierte Untertitel, Grafiken, die deine Aussagen belegen, sauberer Ton, Musik darunter.

Video und Ton verlassen den Rechner nie. An Claude gehen nur Text (das Transkript) und kleine Standbilder an Entscheidungspunkten.

> **Stand: frühe Entwicklung (0.0.x).** Installer, Werkzeuge und Selbsttest laufen. Schnitt-Pipeline und App entstehen gerade. Gesamtkonzept: [`docs/BRIEF.md`](docs/BRIEF.md), Fortschritt: [`CHANGELOG.md`](CHANGELOG.md).

## Installation

```bash
git clone https://github.com/derFlok/video-cut.git
cd video-cut
./start.sh --selftest
```

Voraussetzungen: macOS 14+ auf Apple Silicon, 16 GB RAM empfohlen, rund 15 GB freier Speicher, und Claude Code mit eigenem Login oder ein API-Key (siehe [`docs/AUTH.md`](docs/AUTH.md)).

`start.sh` installiert alles nach `./.tools` (ohne Homebrew, ohne Admin-Rechte). Jeder Download ist auf eine Version festgelegt und per SHA-256 geprüft. Mehrfaches Ausführen ist unbedenklich.

Marken, Projekte und Modelle liegen in `~/VideoCut` (änderbar über `VIDEOCUT_HOME`), nie im Repository.

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

Optionen: `--approve` (ohne Nachfrage), `--replan`, `--recut`, `--brand <id>`, `--screen DATEI`
(gleichzeitig aufgenommene Bildschirmaufnahme, für einen neuen Auftrag), `--asset DATEI...`
(Screenshots für einen neuen Auftrag; als letzte Option angeben), `--demo-data` (gefundene
persönliche Daten sind Demodaten), `--format 9:16|16:9|1:1|4:5` (Ausgabeformat; bei einem
bestehenden Auftrag ersetzt es das Format und schneidet neu), `--max-turns N`. Zeit, Turns,
Kosten und Tokens jedes Claude-Laufs stehen in `work/runs.jsonl`.

**Ausgabeformat** (je Video): `9:16` 1080×1920 (Reels, Shorts, Stories), `16:9` 1920×1080
(YouTube, Website, Präsentationen), `1:1` 1080×1080 und `4:5` 1080×1350 (Feed-Beiträge).
Standard: `16:9`, wenn der Auftrag eine Bildschirmaufnahme oder Screenshots hat
(Produktdemo), sonst `9:16`. Später ändern mit `videocut tool job format <id> 16:9`;
`videocut tool job show <id>` zeigt es an.

## Produktdemo aufnehmen

```bash
.venv/bin/videocut run ~/Movies/ich.mov --screen ~/Movies/bildschirm.mov --brand meine-marke
.venv/bin/videocut run ~/Movies/ich.mov --brand meine-marke --asset ~/Desktop/shot-1.png ~/Desktop/shot-2.png
```

1. **Bildschirm:** Cmd+Shift+5 drücken, „Gesamten Bildschirm aufnehmen" (oder einen Teil)
   wählen und unter *Optionen* dein **Mikrofon** wählen und **Mausklicks anzeigen**
   einschalten. Über die Mikrofonspur legt Video Cut Bildschirm und Kamera übereinander.
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
   Ohne Antwort wird alles Gelistete mit vollen Flächen abgedeckt.
5. Ist die Synchronisierung unsicher, hält der Lauf an und erklärt, wie du sie von Hand
   setzt: `videocut tool sync --job <id> --offset <Sekunden>` (Bildschirmzeit minus
   Kamerazeit eines Moments, den man in beiden sieht oder hört), dann `videocut run <id>`.

**Nur Screenshots:** Screenshots machen (Cmd+Shift+4), dich beim Erklären aufnehmen und den
zweiten Befehl starten. Claude ordnet jeden Screenshot dem Satz zu, der ihn erklärt, und
zeigt ihn mit Zoom auf die genannte Stelle; persönliche Daten darin werden abgedeckt.

## Marken

Video Cut bringt kein eigenes Branding mit. Du lädst deins hoch (Logo, Farben, Schriften, optional Brand-Guide und Beispielposts), und jedes Video passt sich daran an. Mehrere Marken nebeneinander sind möglich, pro Video wählst du eine.

Eine Marke ist ein Ordner `~/VideoCut/brands/<id>/` mit `brand.json` und ihren Schrift- und
Logodateien (Vorlage: `brands/_example`).

```bash
.venv/bin/videocut tool brand check meine-marke   # prüfen; Kontrasttabelle (Untertitel ≥ WCAG AA)
.venv/bin/videocut tool brand sheet meine-marke   # Vorschau-Bogen aller Szenen → brands/meine-marke/preview.jpg
```

## Lizenz

Apache-2.0. Unabhängiges Projekt, nicht mit Anthropic verbunden.
