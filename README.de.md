# Video Cut

**Kurzvideos aus deinen eigenen Aufnahmen, geschnitten auf deinem Mac.** Du nimmst dich beim Sprechen auf, legst die Datei rein, wählst Videoart und Marke. Video Cut macht daraus ein fertiges Kurzvideo: Füllwörter und Pausen raus, Hochformat 9:16 (oder 16:9, 1:1, 4:5 je Video), animierte Untertitel, Grafiken, die deine Aussagen belegen, sauberer Ton, Musik darunter. Für den Schnitt startet Video Cut Claude Code, das Programm von Anthropic, auf deinem eigenen Mac.

Deine Videos und dein Ton verlassen den Mac nie.

> **Stand: frühe Entwicklung (0.0.x).** Installer, Schnitt-Pipeline und die lokale App laufen; mit Ecken und Kanten ist zu rechnen. Gesamtkonzept: [`docs/BRIEF.md`](docs/BRIEF.md), Fortschritt: [`CHANGELOG.md`](CHANGELOG.md).

[English](README.md)

## In drei Schritten loslegen

Du brauchst keine technischen Kenntnisse. Claude Code übernimmt die Installation und erklärt dir jeden Schritt.

*Du hilfst bei der Installation, als Mensch oder als Claude Code? Lies [`INSTALL.md`](INSTALL.md) (Englisch).*

1. **Claude Code installieren und anmelden.** Claude Code ist Anthropics Assistenz-Programm für deinen Computer: Lade die [Claude-Desktop-App](https://claude.com/download) herunter und öffne darin den Bereich *Code*, oder installiere [Claude Code](https://code.claude.com/docs/de/quickstart) direkt. Melde dich mit deinem Claude-Konto (Pro- oder Max-Abo) an, oder mit einem API-Key, wenn du lieber pro Video zahlst (siehe unten).
2. **Diesen Satz in Claude Code einfügen:**

   > Installiere https://github.com/derFlok/video-cut für mich und führe mich durch die Einrichtung.

3. **Den Anweisungen von Claude Code folgen.** Es lädt Video Cut nach `~/VideoCut/app`, installiert alles Nötige (es lädt rund 2,5 GB herunter und belegt rund 4 GB Speicher), legt **Video Cut** in den Ordner *Programme* in deinem Benutzerordner und öffnet es.

Ab dann startest du Video Cut mit einem Doppelklick auf **Video Cut** in `~/Applications` (Finder: *Programme* in deinem Benutzerordner); ein Terminal brauchst du nie. Wenn etwas nicht klappt oder du aktualisieren willst, sag es einfach Claude Code, z. B. *„Video Cut startet nicht“* oder *„Video Cut aktualisieren“*.

## Claude verbinden: zwei Wege

Beim ersten Start prüft Video Cut im Hintergrund, ob dein Claude Code angemeldet ist, und lässt dich wählen:

- **Dein Claude-Abo (Pro oder Max):** keine Zusatzkosten; jedes Video verbraucht einen Teil der Nutzungsgrenzen deines Abos. Nur für dich selbst, auf deinem eigenen Mac, mit deinem eigenen Konto (Anthropics Bedingungen). Für geschäftliche Videos, im Team oder in einer Firma nimm einen API-Key.
- **Ein API-Key:** Du erstellst einen Key in der [Claude Console](https://platform.claude.com) (früher Anthropic Console) und zahlst pro Video. Video Cut bewahrt den Key im Schlüsselbund deines Macs auf.

Details und die Regeln dahinter: [`docs/AUTH.md`](docs/AUTH.md) und [`docs/TERMS-REVIEW.md`](docs/TERMS-REVIEW.md) (Englisch).

## Was deinen Mac verlässt

Nur das, nur an Anthropic, und nur über deinen eigenen Claude-Zugang:

- der **Text des Transkripts** deiner Aufnahme,
- **verkleinerte Standbilder** an den Stellen, an denen Claude etwas entscheiden muss,
- **auf deinem Mac per Texterkennung gelesener Bildschirmtext**, wenn du eine Bildschirmaufnahme, Screenshots oder Folien dazugibst.

Nur wenn du für ein Video **Material** einschaltest (standardmäßig aus): die englischen
Suchwörter jedes Wunschs gehen an die Bildarchive, die du aktiviert hast (Pexels, Coverr),
mit Ausrichtung und Sprache, und die Bildbeschreibung in einem Satz plus die Farbcodes deiner
Marke an den gewählten Bildgenerator (Google Gemini oder OpenAI), mit deinen eigenen
Schlüsseln. Nie eine Aufnahme, ein Standbild, ein Logo oder ein Name.

Aufnahmen, Ton und fertige Videos bleiben auf deinem Mac. Video Cut schickt nichts an andere: keine Telemetrie, keine Statistiken, kein Konto. Downloads gibt es nur bei Installation und Updates (die Werkzeuge und Modelle aus [`manifest/tools.json`](manifest/tools.json)). Claude Code selbst ist Anthropics Programm und folgt Anthropics eigenen [Regeln zur Datennutzung](https://code.claude.com/docs/de/data-usage).

## Was es kostet

Video Cut ist kostenlos und Open Source. Claude bezahlst du über deinen eigenen Zugang:

- **Abo:** keine Zusatzkosten; wird auf die Nutzungsgrenzen deines Abos angerechnet.
- **API-Key** (gemessen an unseren Testvideos, Oktober 2026): rund **1–3,50 USD** pro Video bis zur ersten fertigen Fassung, rund 0,40–1,50 USD je Korrektur danach; lange Bildschirm-Demos mit mehreren Korrekturrunden kamen auf 6–9 USD. Die App zeigt die Kosten jedes Videos schon während der Arbeit.

## Voraussetzungen

- Ein Mac mit Apple-Chip (M1 oder neuer); Intel-Macs werden nicht unterstützt
- macOS 14 Sonoma oder neuer
- 16 GB Arbeitsspeicher empfohlen
- Rund 4 GB Speicher für die Installation (rund 2,5 GB werden heruntergeladen), dazu Platz für deine Videos (der Installer braucht mindestens 8 GB frei und empfiehlt 15 GB)
- Claude Code, angemeldet mit einem Claude-Pro- oder -Max-Abo oder einem Anthropic-API-Key
- Optional, lokale Bilderzeugung (in der App unter Einstellungen → Material, oder `videocut models pull --image`): 16 GB Arbeitsspeicher und rund 8 GB zusätzlicher Speicher (16 GB Download, 26 GB frei während der Installation)

## Deinstallieren

Sag Claude Code *„Deinstalliere Video Cut“*, oder entferne selbst:

| Was | Wo |
|---|---|
| Das Startprogramm | `~/Applications/Video Cut.app` |
| Das Programm mit allen Werkzeugen | `~/VideoCut/app` |
| Deine Marken, Videos und geladenen Sprachmodelle | der Rest von `~/VideoCut` – **enthält deine Arbeit**, nur löschen, wenn du sie nicht mehr brauchst |
| Den API-Key, falls gespeichert | Schlüsselbundverwaltung → nach „videocut“ suchen → löschen |
| Claude Code | bleibt; es ist ein eigenes Programm von Anthropic |

## So funktioniert es

1. Ein lokales Modell schreibt deine Aufnahme Wort für Wort mit Zeitstempeln mit.
2. Claude liest das Transkript, schaut sich nur bei unklaren Entscheidungen Kontaktbögen an und schlägt einen Plan vor (Hook, Kernaussage, Länge, was rausfliegt).
3. Du gibst den Plan frei.
4. Claude schreibt eine Schnittliste. ffmpeg rendert sie, Grafiken entstehen in deiner Marke.
5. Claude prüft das eigene Ergebnis an jedem Schnitt und behebt Probleme (bis zu drei Runden).
6. Du schaust es dir an, gibst Feedback in eigenen Worten und lädst das Ergebnis herunter.

Es ist kein fremder KI-Editor beteiligt. Jeder Schritt ist ein offenes Werkzeug (ffmpeg, Whisper, MediaPipe, DeepFilterNet, Headless-Chromium), das Claude steuert und prüft.

## Installation über die Kommandozeile (Entwickler)

```bash
git clone https://github.com/derFlok/video-cut.git
cd video-cut
./start.sh --selftest
```

`start.sh` installiert alles nach `./.tools` (ohne Homebrew, ohne Admin-Rechte). Jeder Download ist auf eine Version festgelegt und per SHA-256 geprüft ([`manifest/tools.json`](manifest/tools.json)). Mehrfaches Ausführen ist unbedenklich.

| Befehl | Was er tut |
|---|---|
| `./start.sh` | installieren oder auffrischen, dann die Installation prüfen |
| `./start.sh --selftest` | ebenso, dann die Pipeline an einem erzeugten Testclip laufen lassen |
| `./start.sh --ui` | ebenso, dann die App im Browser öffnen |
| `./start.sh --check` | nur prüfen, nichts installieren |
| `./uninstall.sh` | installierte Werkzeuge entfernen (deine Daten bleiben) |
| `./uninstall.sh --all` | zusätzlich anbieten, den Datenordner zu löschen |

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
neuen Plan und eine neue Freigabe. Persönliche Daten auf deinen Seiten (ein Name, eine
E-Mail-Adresse) stehen im Plan wie bei einem Screenshot; was markiert bleibt, wird im
Video abgedeckt.

Solange eine Folie zu sehen ist, liegt keine Karte und kein Stichwort darüber: die Folie
trägt den Inhalt. Untertitel bleiben, wo du neben einer Folie sprichst: im Hoch- oder
Quadratformat stehen sie auf der Linie zwischen Folie und dir, weg vom Folientext und von
deinem Gesicht.

## B-Roll und erzeugte Bilder

Mit **Material** schlägt der Plan ein paar Stellen vor, an denen ein Bild hilft (höchstens
eine pro 8 Sekunden): einen kurzen Archiv-Clip im Vollbild, während du weitersprichst
(B-Roll), ein Archivfoto oder ein erzeugtes Bild. Nach deiner Freigabe sucht Video Cut bei
Pexels und Coverr und/oder fragt den Bildgenerator und zeigt pro Stelle die Kandidaten mit
Lizenz und Urheberangabe; du wählst einen oder „Kein Material", suchst mit anderen Wörtern
neu oder lässt neu erzeugen. Nur was du gewählt hast, kommt ins Video. Erzeugte Bilder sind
in der App als „KI" markiert, ihre Originaldateien behalten das Wasserzeichen des Generators (SynthID / C2PA), und ein
Kostenlimit pro Video (Standard 1 USD) stoppt die Erzeugung, bevor es teuer wird.

Einmal einrichten unter **Einstellungen → Material**: dein Pexels-Schlüssel (kostenlos),
optional Coverr, und ein Gemini- oder OpenAI-Schlüssel für erzeugte Bilder. Die Schlüssel
liegen nur im Schlüsselbund deines Macs.

**Ohne Schlüssel:** Die Karte *Lokales Bildmodell (optional)* an derselben Stelle lädt FLUX.2
[klein] 4B einmal herunter (16 GB, 8 GB bleiben auf dem Mac; braucht 16 GB Arbeitsspeicher und
Apple Silicon) und erzeugt die Bilder dann auf deinem Mac: ohne Konto, ohne Kosten, nichts
verlässt den Mac, rund 40–80 s pro Bild auf einem M3 Pro. Die Karte zeigt freien Speicher und
Arbeitsspeicher, Fortschritt und Tempo des Downloads, lässt dich abbrechen (fertige Teile
bleiben; „Fortsetzen" macht weiter) und das Modell wieder entfernen. Im Terminal geht dasselbe
mit `.venv/bin/videocut models pull --image`.

```bash
.venv/bin/videocut run ~/Movies/ich.mov --material-mode both --material-wish "ruhige Büroszenen"
.venv/bin/videocut tool material show <id>                 # die Kandidaten pro Wunsch
.venv/bin/videocut tool material choose <id> 1 c2          # deine Wahl (oder: none)
.venv/bin/videocut run <id>                                # damit schneiden (--material-skip: keins)
```

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

### Einzelne Wörter korrigieren

Ein Wort im fertigen Video falsch? Öffne **Transkript** auf der Videoseite, klick auf das
Wort, schreib den richtigen Text und drück Enter (Esc bricht ab; leer blendet das Wort in
den Untertiteln aus). Das Video springt an die Stelle. „Immer so schreiben“ trägt die
Schreibweise zusätzlich ins Wörterbuch der Marke ein. Danach setzt **Untertitel neu
setzen** die korrigierten Untertitel ins Video, ohne Claude (ein bis zwei Minuten). Deine
Änderungen bleiben, auch wenn das Wörterbuch erneut angewendet wird, und Claude nimmt sie
als richtig.

```bash
.venv/bin/videocut tool transcript show <job> --from 10 --to 20   # Wörter mit Nummer
.venv/bin/videocut tool transcript edit <job> 84 "Riesen-Golfszene"
.venv/bin/videocut tool transcript undo <job>
```

## Datenschutz

- Aufnahmen, Ton und gerenderte Videos bleiben auf deinem Mac; was an Anthropic geht, steht
  unter [Was deinen Mac verlässt](#was-deinen-mac-verlässt).
- Filme andere Menschen nicht ohne ihre Zustimmung und zeige keine Kundendaten im Bild.

## Lizenz

Apache-2.0, siehe [`LICENSE`](LICENSE). Fremdkomponenten und ihre Lizenzen:
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Video Cut ist ein unabhängiges Projekt. Es ist nicht mit Anthropic verbunden und wird nicht von
Anthropic unterstützt oder empfohlen.
