# AfterLimit

**Dein KI-Coding-Agent hat sein Nutzungslimit erreicht. AfterLimit nimmt die Arbeit in dem Moment wieder auf, in dem das Limit zurückgesetzt wird — während du schläfst, beim Mittagessen bist oder das ganze Wochenende weg bist.**

[English](README.en.md) · [한국어](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · **Deutsch** · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

Du kennst diesen Bildschirm. Dein Agent stoppt mitten in der Aufgabe um 14 Uhr, das Limit wird um 19 Uhr zurückgesetzt, und diese fünf Stunden sind einfach… weg — es sei denn, du sitzt um 19 Uhr zufällig am Terminal, um „continue“ zu tippen.

AfterLimit schließt diese Lücke. Ein winziger Hintergrundjob bemerkt das Zurücksetzen und nimmt deine pausierten Sitzungen automatisch wieder auf. Du kommst zu fertiger Arbeit zurück, nicht zu einem hängenden Prompt.

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← Limit bereits aufgehoben
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← noch 5 Std. 18 Min.
```

## Was es anders macht

**Beobachten kostet keine Tokens.** AfterLimit liest deine lokalen Sitzungsprotokolle — es ruft nie das Modell auf, um nach dir zu sehen. Tokens werden nur in dem Moment ausgegeben, in dem die Arbeit tatsächlich fortgesetzt wird. Alle 5 Minuten nachzusehen ist kostenlos.

**Es umgeht das Limit nicht mit Tricks.** AfterLimit wartet auf das *echte* Zurücksetzen, das die API gemeldet hat, und setzt danach fort. Es umgeht, fälscht oder hämmert den Endpunkt niemals. Ist das Limit noch nicht aufgehoben, zieht es sich zurück und sieht später erneut nach.

**Es setzt den Kontext fort, keinen neuen Prompt.** Es führt `claude --resume <session>` aus, sodass der Agent mit seiner laufenden To-do-Liste und dem Dateizustand intakt weitermacht — kein Kaltstart, der vergessen hat, was er tat.

**Zeitzonenkorrekt, überall.** Die von Claude angezeigte Reset-Zeit („resets 11pm“) hat keine Zeitzone — es ist deine lokale Wanduhrzeit. AfterLimit verankert sie an der Zeitzone der Maschine, sodass sie in Seoul, New York oder Berlin korrekt gelesen wird. (Das ist ein echter Bug naiver Implementierungen: eine fest verdrahtete Zeitzone schickt jeden Nutzer außerhalb davon zur falschen Zeit.)

## Installation

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

Der Installer erkennt dein Betriebssystem und registriert einen Hintergrundjob, der alle 5 Minuten läuft:

- **macOS** → ein `launchd`-LaunchAgent
- **Linux** → ein `systemd --user`-Timer (fällt auf eine `cron`-Zeile zurück, falls systemd fehlt)

Prüfe dann, ob es deine Sitzungen sieht:

```bash
afterlimit scan     # was blockiert ist und wann es aufgehoben wird — führt nichts aus
afterlimit config   # wo es sucht, deine Zeitzone, Benachrichtigungen
```

Voraussetzungen: Python 3.11+ und das `claude`-CLI in deinem `PATH`. **Null Laufzeitabhängigkeiten** — nur Standardbibliothek.

## Funktionsweise

```
alle 5 Min ──► scanne ~/.claude/projects/*.jsonl
                 │
                 ├─ ist die letzte Nachricht ein Limit-Fehler?     ── nein ─► überspringen
                 ├─ Reset-Zeit erkannt und schon vorbei?           ── nein ─► warten
                 ├─ innerhalb der Abkühlzeit schon fortgesetzt?    ── ja ─► überspringen
                 │
                 └─► claude --resume <session>  ──►  benachrichtigen (optionaler Webhook)
```

Jede Absicherung beantwortet die eine Frage, die die Jury stellen wird — *„spammt das nicht einfach das Modell?“* Nein:

| Absicherung | Was sie verhindert |
|---|---|
| Letzte Nachricht muss ein API-Fehler sein | Eine bereits fortgesetzte Sitzung erneut aufnehmen |
| Reset-Zeit muss vorbei sein | Anklopfen, bevor das Limit wirklich aufgehoben ist |
| Eine Wiederaufnahme pro Zyklus (konfigurierbar) | Dass ein Stau blockierter Sitzungen auf einmal auslöst |
| 5-Stunden-Abkühlung pro Sitzung | Dieselbe Sitzung wieder und wieder aufnehmen |
| Einzelinstanz-Sperre | Dass überlappende Scheduler-Läufe doppelt auslösen |
| Altersgrenze (3 Tage) | Längst tote Rückstände wiederbeleben und Tokens verbrennen |

## Konfiguration

Läuft ohne Konfiguration. Zum Ändern lege eine `config.json` in `~/.config/afterlimit/` ab:

```json
{
  "max_resume_per_cycle": 1,
  "resume_cooldown_hours": 5,
  "max_session_age_days": 3,
  "resume_prompt": "Continue the work that was in progress...",
  "webhook_url": "https://hooks.slack.com/services/...",
  "enable_codex": true,
  "codex_sessions_dir": "~/.codex/sessions",
  "codex_bin": "codex"
}
```

Existiert das Codex-Sitzungsverzeichnis nicht (nicht installiert), wird es stillschweigend übersprungen — kein Grund, das hinter einen Schalter zu stellen. Setze `enable_codex` auf `false`, um nur Claude Code zu beobachten (Verhalten vor 0.3.0).

Benachrichtigungen gehen an jeden Webhook, der JSON akzeptiert — Slack, Discord oder deinen eigenen Endpunkt (das Payload-Format wird anhand der URL gewählt). Kein Webhook, keine Benachrichtigungen; sonst ändert sich nichts. Du kannst auch `AFTERLIMIT_WEBHOOK_URL` in der Umgebung setzen.

Vorschau vor der Aktion:

```bash
afterlimit --dry-run run    # zeigt, was es fortsetzen würde, ohne etwas auszuführen
```

## Deinstallation

```bash
./install.sh --uninstall
```

Entfernt den Hintergrundjob und das CLI. Deine Zustandsdateien bleiben unter `~/.local/state/afterlimit`, bis du sie löschst.

## Umfang und Roadmap

AfterLimit setzt **headless**-Sitzungen fort — der Agent muss nicht laufen; es liest die Protokolle und macht mit der Arbeit weiter. Das ist bewusst editor- und terminalunabhängig: Es funktioniert, egal ob du Claude Code aus einem einfachen Terminal, VS Code oder anderswo steuerst.

**Beobachtet sowohl Claude Code als auch Codex CLI.** Es scannt, welche Sitzungsprotokolle auch immer existieren — nur eines zu nutzen oder beide funktioniert gleich. Codex meldet die Fehlerart über ein strukturiertes Feld (`codex_error_info`) statt Freitext — kein Rätselraten per Regex nötig. Codex-CLI-Versionen sind sich auch bei der Protokollform uneinig (ältere loggen den Fehler als eigenständiges Ereignis; neuere verschachteln ihn in einem `task_complete`-Ereignis) — beide werden unterstützt.

Was noch nicht abgedeckt ist, ehrlich benannt:

- **Interaktive TUI-Wiederaufnahme** — „continue“ in einem *aktiven* tmux-Pane drücken, das mitten im Gespräch blockiert ist. Ein früherer Prototyp tat das; er ist tmux-only und fragil, daher bleibt er als künftiger Opt-in-Modus statt halbfertig veröffentlicht.
- **Andere Agents** — Claude Code und Codex sind bereits abgedeckt. Adapter für andere CLIs sind willkommen; der Kern der Limit-Analyse selbst ist agent-unabhängig.
- **Windows-Scheduler** — `install.ps1` registriert einen Task-Scheduler-Job; CLI und Tests laufen unter Windows in CI. Das Einzige, was CI nicht beweisen kann, ist, dass der Task Scheduler auf einem echten Desktop tatsächlich *auslöst* — prüfe das mit `Get-ScheduledTask AfterLimit`.

## Designnotizen

- **Null Laufzeitabhängigkeiten.** Nur Standardbibliothek — nichts zu prüfen, nichts, das bei der Installation kaputtgeht, keine Lizenzverstrickungen.
- **Reiner Kern, getestet.** Limit-Analyse und Sitzungs-Scan sind reine Funktionen ohne I/O, quergeprüft über Seoul / New York / UTC / Berlin, damit die Zeitzonenlogik nicht stillschweigend zurückfällt. `pytest -q`.
- **Der Kern tut eine Sache.** Das Zurücksetzen erkennen, einmal fortsetzen, aus dem Weg gehen.

## Lizenz

[MIT](LICENSE). Nicht mit Anthropic verbunden oder von Anthropic unterstützt.
