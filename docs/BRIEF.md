# Video Cut: Design-Brief

> Ursprünglicher Bau-Brief des Projekts: Ziel, feste Leitplanken, Architektur, Pipeline, Videoarten, Bauphasen mit Abnahmekriterien und Quellen. Was noch offen ist, steht unter „Offene Wetten“. Der tatsächliche Stand des Codes kann davon abweichen; maßgeblich für Abweichungen ist `docs/ARCHITECTURE.md`.

---

## 1. Ziel in einem Absatz

Ein Open-Source-Projekt auf GitHub, das man klont und mit `./start.sh` startet. Dann läuft auf `http://localhost:4300` eine kleine App. Dort wählt man eine **Videoart**, lädt eine selbst aufgenommene **Talking-Head-Aufnahme** hoch, optional dazu **Screenshots/Bildschirmaufnahmen/Daten**, ein **Branding-Profil** und **Referenz-Reels**, schreibt einen Satz Anweisung und schickt ab. Claude (über Claude Code im Headless-Modus) schneidet das Video **selbst**: Es liest das wortgenaue Transkript, sieht sich an Entscheidungspunkten Bildkacheln an, schreibt eine Schnittliste (EDL), lässt lokal rendern und prüft den eigenen Render an jedem Schnitt. Heraus kommt ein fertiges 9:16-Instagram-Reel (MP4 + Cover). Video und Ton verlassen den Rechner nie; an Claude gehen nur Text und verkleinerte Einzelbilder.

Das Werkzeug ist **komplett markenneutral**. Es enthält kein eingebautes Branding. Der Nutzer lädt sein Branding hoch (Logo, Farben, Schriften, optional Brand-Guide oder Beispielposts), und jedes Reel passt sich daran an. Ein Nutzer kann mehrere Marken pflegen und pro Auftrag eine wählen, z. B. drei völlig verschiedene Unternehmen mit eigenem Look. Zielplattform zuerst: macOS auf Apple Silicon; Oberfläche Deutsch und Englisch.

---

## 2. Leitplanken (nicht verhandelbar)

1. **Kein KI-Editor-SaaS.** Kein Descript, Opus Clip, Captions.ai, Submagic o. ä. Claude ist der Editor. Alle Werkzeuge sind CLIs oder Bibliotheken, die Claude per Bash aufruft und deren Ergebnis eine prüfbare Datei ist.
2. **Lokal zuerst (DSGVO).** Transkription, Audio, Gesichtstracking, Rendern laufen lokal. Kein Audio-Upload in US-Dienste. Deshalb **nicht** ElevenLabs Scribe (video-use-Default, US; EU nur Enterprise).
3. **Claude sieht keine Videos, nur Bilder.** Muster: Transkript zuerst, Bildkacheln nur an Entscheidungspunkten. Pixel messen deterministische Werkzeuge (MediaPipe, ffmpeg), Claude urteilt.
4. **Mensch-Tor vor dem Schnitt.** Claude schlägt erst eine Strategie vor (Hook, Kernaussage, Länge, was raus muss). Geschnitten wird erst nach Freigabe in der UI.
5. **Keine Lizenzkosten.** **Kein Remotion** (für Firmen ab 4 Personen mind. 100 $/Monat). Overlays über eigenen Canvas-Renderer (siehe §8) oder HyperFrames (Apache-2.0).
6. **Name und Anmeldung (Anthropic-Bedingungen):**
   - „Claude Code“ darf **nicht** im Produktnamen stehen.
   - Die App **liest, speichert oder leitet niemals OAuth-Tokens** weiter. Sie startet nur das offizielle, unveränderte `claude`-Programm, mit dem sich der Nutzer selbst angemeldet hat.
   - Zwei Anmeldemodi, umschaltbar per Einstellung (siehe §5). Öffentlich dokumentiert und offiziell unterstützt ist nur der **API-Key-Modus**.
7. **Rechtliches im Produkt verankert:**
   - Keine KI-Avatare, keine geklonte Stimme, keine fotorealistische KI-B-Roll als Standard. Falls je optional eingebaut: automatisch sichtbare Kennzeichnung „KI-generiert“ (AI Act Art. 50 Abs. 4/5, gilt seit 02.08.2026). Schnitt, Untertitel, Entrauschen, Reframing und stilisierte Grafiken sind **nicht** kennzeichnungspflichtig.
   - Musik wird **eingebrannt**, nur kommerziell freie Quellen (Pixabay Music, Freesound CC0). Business-Accounts dürfen auf Instagram nur die Meta Sound Collection nutzen. Lizenznachweis pro Projekt im Job-Ordner archivieren.
   - Screenshots: Hinweis in der UI „Keine echten Kundendaten“; optionaler Schwärz-Schritt (Telefonnummern, Kennzeichen, Namen) vor dem Einblenden.
8. **Null Branding im Code.** Keine Farbe, keine Schrift, kein Logo, kein Firmenname, keine branchenspezifischen Texte im Repo. Jeder sichtbare Stil (Farben, Schriften, Logo, Caption-Look, Bewegungscharakter, Tonalität, Ansprache Du/Sie, verbotene Wörter, Musik-Stimmung) kommt aus dem hochgeladenen Branding-Profil (§9). Die Szenen-Bausteine lesen ausschließlich Design-Tokens. Das Repo bringt höchstens ein neutrales Beispielprofil mit (graustufig, Systemschrift, Platzhalter-Logo), damit die App ohne Upload startet. Testmarken für die Entwicklung liegen in einem gitignorten Ordner, nie im Repo.

---

## 3. Was Anthropic zur Subscription sagt (Stand 10/2026, geprüft)

- **Eigene Nutzung:** Ein Nutzer darf sich mit seiner eigenen Subscription im unveränderten Claude-Code-Programm anmelden; `claude -p` (Headless) ist offiziell dokumentiert. Kontingente sind auf „normale, individuelle Nutzung“ ausgelegt. Ein paar Reels pro Woche sind das, eine Dauer-Render-Fabrik nicht.
- **Drittprodukte:** Sinngemäß aus der Doku: *„Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication methods…“* sowie: Anthropic erlaubt Drittentwicklern nicht, *„to route requests through Free, Pro, or Max plan credentials on behalf of their users“*.
- **Grauzone:** Die App ruft nur das vom Nutzer selbst installierte und eingeloggte `claude` auf. Vor einer Veröffentlichung, die das als Feature bewirbt: **schriftlich bei Anthropic nachfragen.** Die Verbraucher-Bedingungen und die AUP wurden nur über Zusammenfassungen geprüft, vor Veröffentlichung im Original lesen.
- **Konsequenz für den Bau:** Beide Modi von Anfang an einbauen. Im README: API-Key ist der unterstützte Weg; „eigener Claude-Code-Login“ heißt dort „Lokaler Modus (nur eigene Nutzung)“, mit Hinweis.

---

## 4. Architektur

```
video-cut/
├── start.sh                 # Einstieg: prüft/installiert alles, startet App, öffnet Browser
├── app/                     # Backend (Python 3.12, FastAPI, uvicorn)
│   ├── main.py              # REST + SSE-Fortschritt, liefert Frontend statisch aus
│   ├── jobs.py              # Warteschlange (SQLite), ein Job zur Zeit
│   └── claude_runner.py     # startet `claude -p` pro Job/Phase, streamt Events
├── web/                     # Frontend (Vite + React, statisch gebaut)
├── pipeline/                # deterministische Werkzeuge, je eine CLI
│   ├── ingest.py            # ffmpeg: Probe, Audio 48 kHz extrahieren, Proxy
│   ├── denoise.py           # DeepFilterNet
│   ├── transcribe.py        # mlx-whisper / parakeet-mlx → words.json + takes_packed.md
│   ├── face.py              # MediaPipe Face Landmarker → face.json (Mitte, Blinzeln, Blick)
│   ├── timeline_view.py     # PNG: Filmstreifen + Wellenform + Wortlabels für [t0,t1]
│   ├── sheets.py            # Kontaktabzüge 3×3 und Burst-Bögen ±0,5 s
│   ├── render_edl.py        # EDL → ffmpeg (Schnitt, Crop/Zoom, Overlays, Musik, loudnorm)
│   └── verify.py            # Prüfbögen an jedem Schnitt/Caption-Wechsel
├── renderer/                # Node 20 + playwright-core: Canvas-Overlays, transparent
│   ├── render.js
│   └── scenes/              # Szenen-Bausteine (Caption, Zahlenkarte, Screenshot-Frame …)
├── .claude/skills/          # Skills für die Claude-Läufe (Fork von video-use + eigene)
├── templates/               # Reel-Verträge je Videoart (YAML), siehe §7
├── brands/_example/         # einziges mitgeliefertes Profil, neutral
├── music/                   # lokale CC0-Bibliothek + Lizenzdateien
└── (Nutzerdaten außerhalb des Repos: ~/VideoCut/{brands,jobs,music,models})
```

**Entscheidungen (begründet, nicht neu diskutieren):**
- **Python für Backend und Pipeline**, weil mlx-whisper, parakeet-mlx, MediaPipe und DeepFilterNet Python sind. Umgebung über **uv** (`uv sync`), kein Homebrew-Zwang.
- **Node nur für den Renderer**, weil der vorhandene Canvas-Renderer (siehe §8) headless Chromium braucht.
- **ffmpeg** als statisches Binary (z. B. `ffmpeg-static` bzw. heruntergeladen nach `./.tools/`), Version beim ersten Start prüfen.
- **Ein Job zur Zeit.** Kontingente und Rechenlast vertragen keine Parallelität.
- **Plattform:** macOS Apple Silicon zuerst. Linux später, Windows nicht.

### start.sh muss beim ersten Start
1. Apple Silicon und macOS-Version prüfen.
2. `uv` installieren falls fehlend, `uv sync`.
3. Node prüfen/installieren (lokal, z. B. über `fnm` in `./.tools/`), `npm ci` in `renderer/`, Chromium für Playwright laden.
4. ffmpeg nach `./.tools/` holen, falls keins mit libx264, libass und ProRes vorhanden.
5. Modelle vorladen: Whisper-Modell (mlx), MediaPipe `face_landmarker.task`, DeepFilterNet. Fortschritt anzeigen.
6. `claude` prüfen: installiert? `claude --version`. Eingeloggt? (Nur prüfen, nie Token lesen.) Falls nein: Anleitung zeigen bzw. auf API-Key-Modus verweisen.
7. App starten, `open http://localhost:4300`.
Jeder Schritt idempotent; zweiter Start dauert Sekunden.

---

## 5. Wie Claude angesteuert wird

- Pro Job-Phase ein Lauf: `claude -p "<Auftrag>" --output-format stream-json --verbose` mit Arbeitsverzeichnis `jobs/<id>/`, Skills aus dem Repo, eingeschränkter Tool-Liste (`--allowedTools` Bash, Read, Write, Edit) und Turn-Limit. **Flags vor dem Bau mit `claude --help` gegen die installierte Version prüfen.**
- Zwei Phasen mit Mensch-Tor dazwischen:
  1. **Plan-Lauf:** liest Transkript + Kontaktabzug + Reel-Vertrag + Anweisung → schreibt `project.md` (Hook, Kernaussage, Ziel-Länge, Streichliste, Overlay-Ideen mit Zeitstempeln). UI zeigt das, Nutzer gibt frei oder korrigiert.
  2. **Schnitt-Lauf** (mit `--resume <session-id>` oder frisch mit `project.md` als Eingabe): EDL schreiben → rendern → Selbstprüfung, max. 3 Runden → `out/`.
  3. **Korrektur-Läufe:** Freitext-Feedback aus der UI → EDL anpassen → neu rendern.
- `stream-json`-Events in der UI als Fortschritt zeigen (Transkript fertig, Schnittvorschlag, Grafiken, Prüfrunde 2 …).
- **Anmeldemodi:**
  - *Lokaler Modus:* `claude` wird ohne Zusatz-Env gestartet und nutzt den Login des Nutzers.
  - *API-Key-Modus:* Key wird im macOS-Schlüsselbund gespeichert (nicht im Repo, nicht in Klartext-Dateien), beim Start als `ANTHROPIC_API_KEY` in die Umgebung des Kindprozesses gegeben. Kosten pro Reel in der UI aus den Usage-Events anzeigen.
- Große Bildmengen (Prüfbögen, Referenzvideo-Analyse) an Sub-Agenten geben, die nur Text zurückliefern, damit der Hauptkontext klein bleibt.

---

## 6. Die Pipeline (10 Schritte)

Basis ist **browser-use/video-use** (MIT, Claude-Code-Skill): Transkript → Packen → Strategie → EDL → Render → Selbstprüfung. **Forken**, Scribe durch lokale Transkription ersetzen, Reel-Stufen ergänzen.

| # | Schritt | Werkzeug | Rolle von Claude |
|---|---|---|---|
| 1 | Ingest, Audio 48 kHz extrahieren, Proxy 360×640 | ffmpeg | — |
| 2 | Ton säubern | DeepFilterNet (`deep-filter`) | prüft Pegel/Spektrum vorher/nachher als Zahlen |
| 3 | Transkript mit Wortzeiten + Pausenmarkern → `takes_packed.md` (~12 KB) | mlx-whisper (`word_timestamps=True`) oder parakeet-mlx | liest vollständig |
| 4 | Gesichtsspur, Blinzeln, Blick je Frame (5 fps reicht für Mitte) | MediaPipe Face Landmarker (Videomodus, Blendshapes) | nutzt es als Nebenbedingung |
| 5 | Strategie: Hook, Kernaussage, Länge, Streichliste | — | schreibt `project.md`, **wartet auf Freigabe** |
| 6 | Schnitt: Füllwörter, Versprecher, Retakes (letzter vollständiger Take gewinnt), Pausen auf ~0,3 s; Schnitte an Wort-/Pausengrenzen, 30-ms-Fades, nie im Lidschlag | ffmpeg + Burst-Bögen | schreibt `edl.json` |
| 7 | 9:16-Reframing (Median der Gesichtsmitte, fester Ausschnitt, nur bei großer Bewegung nachführen) + Zooms auf Betonungen (1,25× normal, bis 1,6× kritisch), Bildwechsel alle 2–4 s | ffmpeg `crop`/`scale` | markiert Betonungen im Transkript |
| 8 | Captions + Overlays (Zahlen, Keywords, Belege, Screenshots) + B-Roll | Canvas-Renderer (transparent) | schreibt Overlay-Szenen, je Szene ein Sub-Agent |
| 9 | Musik einbrennen + `sidechaincompress`-Ducking, loudnorm 2-Pass −14 LUFS / −1 dBTP, Mux, Export | ffmpeg | — |
| 10 | Selbstprüfung: Bögen an jedem Schnitt (±2 Frames) und jedem Caption-Wechsel | `verify.py` + Claude | prüft Bildsprünge, Knackser, Caption im Gesicht/in UI-Zone, abgeschnittene Wortanfänge; max. 3 Runden |

**Bild-Budget:** Tokens ≈ ⌈B/28⌉×⌈H/28⌉ pro Bild. 1080×1920 ≈ 2.700 Tokens. Daher Kacheln 360×640, Bögen 3×3 = 1080×1920 mit eingebranntem Zeitstempel + Framenummer pro Kachel. Ab >20 Bildern im Kontext max. 2000 px pro Seite. Jedes per Read geladene Bild wird in späteren Turns erneut mitgeschickt → Bilder in Sub-Agenten.

**Füllwort-Fallback (ungetestet, bauen):** Lücken zwischen Wortzeitstempeln, in denen trotzdem Stimmenergie liegt, als Füllwort-Kandidaten markieren; Claude entscheidet per Burst-Bogen mit Wellenform.

### EDL-Schema (einzige Wahrheitsquelle)
```json
{
  "fps": 30, "size": [1080, 1920],
  "source": "input/take.mov",
  "segments": [{ "in": 12.48, "out": 17.92, "fade_ms": 30, "crop": {"cx": 0.52, "cy": 0.41, "zoom": 1.0}, "zoom_keys": [[14.1, 1.25]] }],
  "captions": [{ "t0": 0.0, "t1": 1.2, "words": [{"w": "Dreißig", "t0": 0.0, "t1": 0.4, "emph": true}] }],
  "overlays": [{ "scene": "number_card", "t0": 3.2, "t1": 5.8, "props": {"value": "30 %", "label": "Beispiel-Label"} }],
  "broll": [{ "file": "input/screen1.mp4", "t0": 8.0, "t1": 11.5, "layout": "split" }],
  "music": { "file": "music/x.mp3", "gain_db": -18, "duck": true, "license": "music/x.LICENSE.txt" }
}
```
Zeiten der Segmente in Quellzeit, alles andere in Ausgabezeit. Optionaler Export als FCPXML für Feinschliff in Final Cut (später).

---

## 7. Videoarten (Karten in der UI, je ein Reel-Vertrag in `templates/`)

Jeder Vertrag legt fest: Länge, Hook-Regel, Caption-Stil, erlaubte Overlay-Szenen, Pflicht-Inputs, Abnahmekriterien. Allgemein: 30–60 s (45–60 s stärkstes Band bei Business-Reels), Hook in den ersten 2 s ohne „Hallo, ich bin …“, CTA aufs Weiterleiten („Schick das jemandem, der …“; Wortlaut und Du/Sie aus dem Branding-Profil). Die Verträge sind branchenneutral; die Beispiele unten sind nur zur Illustration.

**Stufe 1 – nur Aufnahme**
1. Gedanke des Tages (30–45 s, eine These, Kernaussage groß eingeblendet)
2. Mythos vs. Realität (Split-Grafik)
3. Tagebuch / Behind the Scenes (Kapitelmarken)
4. Kundenfrage beantworten (Frage als Chat-Bubble)
5. Drei Tipps / Liste (animierte Nummer pro Punkt)

**Stufe 2 – plus Screenshots / Bildschirmaufnahmen / Fotos**
6. Produkt- oder Angebots-Walkthrough (Bildschirmaufnahme/Fotos mit Zoom auf die besprochene Stelle)
7. Neuheit / Release (Vorher/Nachher)
8. Prozess als Bildstrecke (mehrere Screenshots/Fotos als Klickstrecke animiert)
9. Kundenstimme / Case (Screenshot hervorgehoben, Namen/Nummern geschwärzt)

**Stufe 3 – plus Daten oder Audio**
10. Zahlen-Video (CSV/JSON → animierte Diagramme)
11. Audio-Ausschnitt mit mitlaufendem Transkript + Tonwelle (z. B. Gesprächs- oder Podcast-Ausschnitt; nur mit Einwilligung aller Stimmen)
12. Reaktion / Kommentar (Screenshot oben, Sprecher unten)

**Stufe 4 – Kombination / Recycling**
13. Ein langes Video → 3–5 Reels (Claude wählt die stärksten Passagen, je eigener Hook)
14. Erklärvideo als reine Animation mit Voiceover
15. Serien-Format (festes Intro/Design, wöchentliche Folge)

**Reihenfolge im Bau:** erst Typ 1 (testet den Schnitt), dann Typ 6 (testet Einblendungen), dann Typ 13, der Rest ist Variation.

**Referenzvideos:** Nutzer lädt ein Reel hoch, dessen Stil er mag. Pipeline extrahiert Kontaktabzug + Schnittfrequenz (Szenenwechsel per ffmpeg `scdet`) + Caption-Position; ein Sub-Agent leitet daraus Tempo, Caption-Stil und Zoom-Häufigkeit als Text ab und schreibt sie als Overrides in den Vertrag. Keine Inhalte aus dem Referenzvideo übernehmen.

---

## 8. Overlays und Captions

**Vorhandener Renderer als technischer Startpunkt:** ein bestehender Canvas-Renderer des Maintainers (nicht Teil dieses Repos). **Nur die Technik wird neu umgesetzt** (Render-Loop, Federn, Export, Kontaktabzug), ohne Farben, Schrift, Logo oder Texte einer Marke; alles Sichtbare kommt aus Design-Tokens des Branding-Profils. Prinzip: Canvas mit reiner `draw(t)`, Bild für Bild in headless Chromium, nach ffmpeg gepipet; 60 fps, 6 Subframes Motion Blur; Kontaktabzug per `--sheet`, Standbilder per `--still`. Bewegungsprinzip „verbove“ (markenneutral, als eine mögliche `motion.character`-Ausprägung): eine Form, nie geschnitten, geschlossene Federn auf einem 120-BPM-Raster, Inhalt erscheint nach dem Morph und geht vor dem nächsten, Blur-Übergänge.

Feder-Kern (übernehmen, inkl. Fix für kritische Dämpfung):
```js
function step(tau, w = 13, z = 0.8) {
  if (tau <= 0) return 0;
  if (z >= 1) return 1 - Math.exp(-w * tau) * (1 + w * tau);
  const wd = w * Math.sqrt(1 - z * z);
  return 1 - Math.exp(-z * w * tau) * (Math.cos(wd * tau) + (z * w / wd) * Math.sin(wd * tau));
}
```

**Nötige Änderung:** `render.js` kodiert heute deckend nach H.264/yuv420p. Für Overlays: transparenter Canvas, Ausgabe als **ProRes 4444 (`-c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le`) oder PNG-Sequenz**, dann ffmpeg `overlay` auf das geschnittene Video. Auflösung 1080×1920, 30 fps (Reel-Ziel), Motion Blur optional.

**Szenen-Bibliothek (erste Version):** `caption` (Wortgruppen nach Pausen, aktives Wort hervorgehoben, wie Remotions `createTikTokStyleCaptions()`, aber selbst gebaut, ~50 Zeilen) · `number_card` · `keyword` · `list_item` · `chat_bubble` · `screenshot_frame` (mit Zoom-Fokus und optionaler Schwärzung) · `split_compare` · `bar_chart` · `lower_third` · `cta_end`.

**Safe Zones (9:16):** organisch oben ~250 px, unten ~350 px frei; für spätere Werbeschaltung Meta-Ads-Zonen 14 % oben, 35 % unten, 6 % seitlich. Captions in die Mitte, nie übers Gesicht (Gesichts-Box aus `face.json`).

---

## 9. Branding: hochladen, ableiten, anwenden

Das Branding ist das zentrale Anpassungsmerkmal. Ziel: Nutzer lädt seine Marke einmal hoch, danach sieht jedes Reel aus, als hätte es die eigene Designabteilung gebaut.

**Upload in der UI (Bereich „Marken“, beliebig viele):**
- Pflicht: Name, Logo (SVG/PNG, hell und/oder dunkel).
- Optional: Farben (Picker oder Hex), Schriftdateien (woff2/ttf/otf), Brand-Guide (PDF/Markdown), 3–10 Beispielbilder (eigene Posts, Website-Screenshots), Icon-Stil, Musik-Stimmung, Tonalität (Du/Sie, locker/seriös), verbotene Wörter, Pflicht-Hinweise (z. B. Impressum-Zeile), Standard-CTA.

**Ableiten (Claude-Lauf „Marke einrichten“):** Fehlt etwas, leitet ein Claude-Lauf es aus Guide und Beispielbildern ab (Farben aus Logo/Bildern per deterministischer Palette-Extraktion, Claude ordnet Rollen zu: Hintergrund, Text, Akzent, Fläche; Schrift-Charakter, Bewegungs-Charakter, Tonalität). Ergebnis ist ein `brand.json` als Vorschlag, den der Nutzer in der UI prüft und korrigiert. Keine Schrift aus dem Netz nachladen ohne Lizenzhinweis; fehlt eine Schriftdatei, eine ähnliche freie Schrift vorschlagen (Google Fonts, OFL) und das kennzeichnen.

**`brand.json` (Design-Tokens, einzige Stilquelle der Szenen):**
```json
{
  "name": "…",
  "colors": { "bg": "#…", "surface": "#…", "text": "#…", "muted": "#…", "accent": "#…", "accent_usage": "sparsam|frei" },
  "fonts": { "display": "fonts/…woff2", "body": "fonts/…woff2", "weights": [400, 600, 800] },
  "logo": { "light": "logo-light.svg", "dark": "logo-dark.svg", "placement": "intro|outro|corner|none" },
  "captions": { "style": "box|outline|plain", "case": "normal|upper", "highlight": "accent|underline|scale", "position": "center|lower" },
  "motion": { "character": "ruhig|federnd|knackig", "spring": { "w": 13, "z": 0.8 }, "transitions": "blur|slide|cut" },
  "icons": "lucide|phosphor|none",
  "voice": { "address": "du|sie", "tone": "…", "forbidden_words": [], "cta": "…" },
  "music": { "mood": "…", "max_bpm": 120 },
  "legal": { "footer": "…" },
  "redaction": { "style": "auto|blur|crop|block" }
}
```

**Anwenden:** Alle Szenen (§8) und das Cover lesen nur diese Tokens. Kontrast automatisch prüfen (Caption-Text vs. Hintergrund mindestens WCAG AA); passt eine Markenfarbe nicht, eine dunklere/hellere Variante ableiten statt unlesbar zu rendern. Vorschau-Seite pro Marke: ein Beispiel-Bogen mit allen Szenen im Markenlook, damit man vor dem ersten Reel sieht, ob es passt.

**Abnahme dafür:** Drei sehr unterschiedliche Testmarken (z. B. hell/minimal, dunkel/laut, verspielt/bunt) erzeugen aus demselben Rohclip drei Reels, die sofort erkennbar zur jeweiligen Marke gehören, ohne eine Zeile Code zu ändern.

---

## 10. Audio, Musik, Export

- DeepFilterNet (48 kHz) → ffmpeg `loudnorm` 2-Pass, Ziel −14 LUFS integriert, −1 dBTP.
- Musik: Pixabay Music / Freesound CC0, lokal in `music/` mit Lizenzdatei; Ducking per `sidechaincompress`; Musik ca. −18 dB unter Sprache.
- Export: 1080×1920, MP4, H.264 (`-pix_fmt yuv420p -movflags +faststart`), AAC ≥128 kbps (besser 192), 30 fps, bis 3 min. Cover-Bild 1080×1920 mit Titel im mittleren 3:4-Ausschnitt (Profilraster).
- Aufnahme-Tipp in der UI: **4K quer oder direkt hochkant aufnehmen.** Aus 1080p quer bleiben für 9:16 nur ~608×1080, jeder Zoom halbiert die Schärfe weiter.

---

## 11. Bauphasen mit Abnahmekriterien

**Phase 0 – Messung (vor allem anderen, ohne UI).**
- Eine Testperson nimmt einen 60–90-s-Clip auf (4K, mit ein paar „äh/ähm“ und einem Versprecher) und annotiert die Füllwörter einmal von Hand (Zeitstempel-Liste).
- mlx-whisper (large-v3/turbo) und parakeet-mlx laufen lassen. Optional CrisperWhisper **nur als Vergleich** (nicht-kommerzielle Gewichte, nicht ausliefern).
- Messen: Recall der Füllwörter je Modell, Wortgrenzen-Abweichung, Laufzeit.
- **Abnahme:** Bericht `docs/messung-transkription.md` mit Zahlen; Modellentscheidung begründet.

**Phase 1 – Kern als CLI.**
- video-use forken (Lizenz + Credits im README), Scribe-Adapter durch `transcribe.py` ersetzen, gleiches `takes_packed.md`-Format.
- Schritte 1–7, 9, 10 lauffähig, `videocut run <job>` von der Kommandozeile.
- **Abnahme:** Der Testclip ergibt ein 9:16-Reel ohne hörbare Füllwörter, ohne Schnitt im Lidschlag, ohne Knackser; die Testperson muss ≤3 Schnitte korrigieren; Tokens und Laufzeit pro Lauf protokolliert.

**Phase 2 – Overlays.**
- Renderer transparent, Szenen-Bibliothek, Captions, Schritt 8.
- Branding-Tokens (§9) von Anfang an: keine Szene darf eine Farbe/Schrift fest einbauen.
- **Abnahme:** Typ 1 und Typ 6 Ende-zu-Ende; Captions nie im Gesicht oder in Safe Zones (automatisch geprüft in `verify.py`); derselbe Clip mit drei Testmarken sieht dreimal klar verschieden aus.

**Phase 3 – Lokale App.**
- FastAPI + React: Videoart-Karten, Upload (Drag & Drop), Marken-Bereich (Upload, „Marke einrichten“-Lauf, Token-Editor, Vorschau-Bogen), Markenwahl pro Auftrag, Referenzvideo, Anweisung, Freigabe der Strategie, Live-Fortschritt (SSE), Vorschau-Player, Feedback → Neu-Render, Download (MP4 + Cover).
- **Abnahme:** Kompletter Durchlauf nur über die UI, ohne Terminal.

**Phase 4 – Installer und Veröffentlichung.**
- `start.sh` idempotent, frischer Mac → laufende App. README mit beiden Anmeldemodi (API-Key = unterstützt; lokaler Modus = nur eigene Nutzung), Lizenzen aller Bausteine, Datenschutz-Hinweis (was an Anthropic geht).
- **Vorher:** Anthropic-Bedingungen im Original lesen, ggf. schriftlich nachfragen; Name ohne „Claude Code“.
- **Abnahme:** Test auf einem zweiten Mac / frischem Benutzerkonto.

Danach: Musik-Auswahl, B-Roll (Pexels-API, `orientation=portrait`, Link auf Pexels Pflicht), Typ 13, FCPXML-Export.

---

## 12. Offene Wetten (messen, nicht annehmen)

1. Behalten lokale Modelle deutsche Füllwörter („äh“, „ähm“)? Whisper glättet sie gern weg. → Phase 0.
2. Erkennt Claude auf 360-px-Kacheln den besseren Take und Mimik? → Phase 1, Korrekturquote.
3. Wie viel Geschmack ohne menschliche Abnahme? → Mensch-Tor bleibt, Korrekturquote messen.
4. Retake-Erkennung ist nirgends dokumentiert → eigene Heuristik (Satz-Ähnlichkeit + „letzter vollständiger Take gewinnt“), testen.
5. Kosten/Laufzeit: geschätzt 0,50–1,50 $ Tokens und 5–15 min pro Reel, **nicht gemessen**.
6. video-use ist populär, hat aber wenige Commits; Fork bewusst schlank halten.

---

## 13. Quellen

- video-use: https://github.com/browser-use/video-use
- Claude Vision (Bildlimits, Token-Formel): https://platform.claude.com/docs/en/build-with-claude/vision
- Claude Code Headless / Agent SDK Auth-Regeln: Claude-Code-Doku (Abschnitte „headless“, „legal and compliance“), Agent-SDK-Overview
- mlx-whisper: https://pypi.org/project/mlx-whisper/ · parakeet-mlx: https://github.com/senstella/parakeet-mlx
- CrisperWhisper (nur Vergleich): https://github.com/nyrahealth/CrisperWhisper
- MediaPipe Face Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python
- DeepFilterNet: https://github.com/Rikorose/DeepFilterNet
- auto-editor (optional Stille-Grobschnitt): https://github.com/WyattBlue/auto-editor
- HyperFrames (Alternative Overlay-Engine, Apache-2.0): HeyGen HyperFrames
- Remotion-Lizenz (warum nicht): https://www.remotion.pro/license
- Talking-Head-Pipeline mit Claude-Code-Skills (Zoom-Logik 40/35/25): https://chrislema.com/claude-code-skills-for-video-editing
- Reframing-Skill-Vorlage: https://clawhub.ai/jianshuo/wjs-reframing-video
- ffmpeg-Filter (`sidechaincompress`, `loudnorm`, `overlay`, `scdet`): https://ffmpeg.org/ffmpeg-filters.html
- Pexels API: https://www.pexels.com/api/documentation/ · Pixabay FAQ: https://pixabay.com/service/faq/
- AI Act Art. 50: https://artificialintelligenceact.eu/article/50/
- Meta-Ads-Safe-Zones: https://adsuploader.com/blog/meta-ads-safe-zones
- Reel-Länge (Buffer, ~140k Reels): https://buffer.com/library/instagram-reels-length

Die ausführliche Recherche ist in `docs/RESEARCH.md` zusammengefasst.

---

## 14. Arbeitsweise

- Phase für Phase. Nach jeder Phase die Abnahmekriterien prüfen und das Ergebnis im CHANGELOG festhalten.
- Keine Marken-, Firmen- oder Personendaten ins Repo (siehe Leitplanke 8). Testmaterial liegt in `~/VideoCut/`, nie im Repo.
