# Video Cut

**Claude schneidet deine Videos, auf deinem Mac.** Du nimmst dich beim Sprechen auf, legst die Datei rein, wählst Videoart und Marke. Video Cut macht daraus ein fertiges Kurzvideo: Füllwörter und Pausen raus, Hochformat 9:16, animierte Untertitel, Grafiken, die deine Aussagen belegen, sauberer Ton, Musik darunter.

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

## Benutzung (Kommandozeile, Phase 1)

```bash
.venv/bin/videocut run ~/Movies/aufnahme.mov --type thought --brief "eine These, starker erster Satz"
```

1. **Vorbereitung** (lokal, ohne Claude): Projektordner in `~/VideoCut/jobs/<id>`,
   Rauschunterdrückung, Transkript mit Wortzeiten, Füllwort- und Pausenkandidaten, Kontaktbögen.
2. **Plan:** Claude liest das Transkript und wenige Standbilder und schreibt `work/project.md`:
   Hook, Kernaussage, Ziel-Länge, was bleibt und was raus fliegt, Betonungen.
3. **Du gibst frei** – im Terminal (vorher `project.md` gern anpassen). Vorher wird nichts
   geschnitten. Später freigeben: `videocut tool job approve <id>`, dann `videocut run <id>`.
4. **Schnitt:** Claude schreibt die Schnittliste, rendert eine Vorschau, prüft jeden Schnitt
   (Knackser, Bildsprünge, Lautheit), korrigiert bis zu drei Runden und rendert
   `out/reel.mp4` (1080×1920, −14 LUFS). `work/cut_report.md` nennt Änderungen und offene Fragen.

Optionen: `--approve` (ohne Nachfrage), `--replan`, `--max-turns N`. Zeit, Turns, Kosten und
Tokens jedes Claude-Laufs stehen in `work/runs.jsonl`.

## Marken

Video Cut bringt kein eigenes Branding mit. Du lädst deins hoch (Logo, Farben, Schriften, optional Brand-Guide und Beispielposts), und jedes Video passt sich daran an. Mehrere Marken nebeneinander sind möglich, pro Video wählst du eine.

## Lizenz

Apache-2.0. Unabhängiges Projekt, nicht mit Anthropic verbunden.
