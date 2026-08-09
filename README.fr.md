# AfterLimit

**Votre agent de codage IA a atteint sa limite d'utilisation. AfterLimit reprend le travail à l'instant même où la limite se réinitialise — pendant que vous dormez, déjeunez ou êtes absent tout le week-end.**

[English](README.en.md) · [한국어](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · **Français** · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

Vous connaissez cet écran. Votre agent s'arrête en pleine tâche à 14 h, la limite se réinitialise à 19 h, et ces cinq heures sont tout simplement… perdues — sauf si vous êtes justement devant votre terminal à 19 h pour taper « continue ».

AfterLimit comble ce vide. Une petite tâche en arrière-plan repère la réinitialisation et reprend automatiquement vos sessions en attente. Vous revenez à un travail terminé, pas à une invite figée.

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← limite déjà levée
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← encore 5 h 18 min
```

## Ce qui le distingue

**La surveillance ne coûte aucun jeton.** AfterLimit lit vos journaux de session locaux — il n'appelle jamais le modèle pour vérifier votre état. Les jetons ne sont dépensés qu'au moment où le travail reprend réellement. Sonder toutes les 5 minutes est gratuit.

**Il ne contourne pas la limite par ruse.** AfterLimit attend la *vraie* réinitialisation signalée par l'API, puis reprend. Il ne contourne, ne falsifie ni ne martèle jamais le point de terminaison. Si la limite n'est pas encore levée, il se retire et revérifie.

**Il reprend le contexte, pas une nouvelle invite.** Il exécute `claude --resume <session>`, de sorte que l'agent poursuit avec sa liste de tâches en cours et l'état des fichiers intacts — pas un démarrage à froid qui a oublié ce qu'il faisait.

**Correct dans tous les fuseaux horaires.** L'heure de réinitialisation affichée par Claude (« resets 11pm ») n'a pas de fuseau — c'est votre heure locale au mur. AfterLimit l'ancre au fuseau de la machine, de sorte qu'elle se lit correctement à Séoul, New York ou Berlin. (C'est un vrai bug des implémentations naïves : coder un fuseau en dur envoie chaque utilisateur hors de ce fuseau à la mauvaise heure.)

## Installation

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

L'installateur détecte votre système d'exploitation et enregistre une tâche en arrière-plan exécutée toutes les 5 minutes :

- **macOS** → un LaunchAgent `launchd`
- **Linux** → un minuteur `systemd --user` (repli sur une ligne `cron` si systemd est absent)

Vérifiez ensuite qu'il voit vos sessions :

```bash
afterlimit scan     # ce qui est bloqué et quand ça se lève — n'exécute rien
afterlimit config   # où il cherche, votre fuseau, les notifications
```

Prérequis : Python 3.11+ et le CLI `claude` dans votre `PATH`. **Zéro dépendance à l'exécution** — bibliothèque standard uniquement.

## Fonctionnement

```
toutes les 5 min ──► scanne ~/.claude/projects/*.jsonl
                       │
                       ├─ le dernier message est-il une erreur de limite ?  ── non ─► ignorer
                       ├─ heure de réinit. lue et déjà passée ?              ── non ─► attendre
                       ├─ déjà repris pendant le refroidissement ?          ── oui ─► ignorer
                       │
                       └─► claude --resume <session>  ──►  notifier (webhook optionnel)
```

Chaque garde-fou répond à la question que poseront les juges — *« ce n'est pas juste du spam vers le modèle ? »* Non :

| Garde-fou | Ce qu'il empêche |
|---|---|
| Le dernier message doit être une erreur d'API | Reprendre une session déjà avancée |
| L'heure de réinitialisation doit être passée | Frapper avant que la limite soit vraiment levée |
| Une reprise par cycle (configurable) | Qu'un lot de sessions bloquées se déclenche d'un coup |
| Refroidissement de 5 h par session | Reprendre la même session encore et encore |
| Verrou d'instance unique | Que des exécutions chevauchantes déclenchent deux fois |
| Limite d'âge (3 jours) | Ranimer un arriéré mort et brûler des jetons |

## Configuration

Fonctionne sans configuration. Pour modifier, déposez un `config.json` dans `~/.config/afterlimit/` :

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

Si le répertoire de sessions Codex n'existe pas (non installé), il est ignoré silencieusement — aucune raison de conditionner cela. Mettez `enable_codex` à `false` pour ne surveiller que Claude Code (comportement pré-0.3.0).

Les notifications vont vers tout webhook acceptant du JSON — Slack, Discord ou votre propre point de terminaison (le format de la charge est choisi selon l'URL). Sans webhook, pas de notifications ; rien d'autre ne change. Vous pouvez aussi définir `AFTERLIMIT_WEBHOOK_URL` dans l'environnement.

Aperçu avant d'agir :

```bash
afterlimit --dry-run run    # montre ce qu'il reprendrait, sans rien exécuter
```

## Désinstallation

```bash
./install.sh --uninstall
```

Supprime la tâche en arrière-plan et le CLI. Vos fichiers d'état restent dans `~/.local/state/afterlimit` jusqu'à ce que vous les effaciez.

## Portée et feuille de route

AfterLimit reprend des sessions **headless** — l'agent n'a pas besoin d'être en cours d'exécution ; il lit les journaux et poursuit le travail. C'est volontairement indépendant de l'éditeur et du terminal : cela fonctionne que vous pilotiez Claude Code depuis un terminal simple, VS Code ou ailleurs.

**Surveille à la fois Claude Code et Codex CLI.** Il scanne les journaux de session qui existent, donc utiliser l'un ou l'autre — ou les deux — fonctionne pareil. Codex signale le type d'erreur via un champ structuré (`codex_error_info`) plutôt qu'en texte libre — pas besoin de deviner avec des regex. Les versions de Codex CLI divergent aussi sur la forme du journal (les anciennes consignent l'erreur comme un événement autonome ; les récentes l'imbriquent dans un événement `task_complete`) — les deux sont prises en charge.

Ce qui n'est pas encore couvert, dit honnêtement :

- **Reprise en TUI interactive** — appuyer sur « continue » dans un volet tmux *actif* bloqué en pleine conversation. Un prototype antérieur le faisait ; réservé à tmux et fragile, il est donc laissé comme futur mode optionnel plutôt que publié à moitié.
- **Autres agents** — Claude Code et Codex sont couverts. Les adaptateurs pour d'autres CLI sont bienvenus ; le cœur d'analyse de la limite lui-même est indépendant de l'agent.
- **Planificateur Windows** — `install.ps1` enregistre une tâche Task Scheduler ; la CLI et les tests tournent sous Windows en CI. La seule chose que la CI ne peut pas prouver, c'est que Task Scheduler se *déclenche* réellement sur un bureau en direct — vérifiez avec `Get-ScheduledTask AfterLimit`.

## Notes de conception

- **Zéro dépendance à l'exécution.** Bibliothèque standard uniquement — rien à auditer, rien qui casse à l'installation, aucun enchevêtrement de licences.
- **Cœur pur, testé.** L'analyse de la limite et le scan des sessions sont des fonctions pures sans E/S, vérifiées de façon croisée à Séoul / New York / UTC / Berlin pour que la logique de fuseau ne puisse pas régresser en silence. `pytest -q`.
- **Le cœur ne fait qu'une chose.** Détecter la réinitialisation, reprendre une fois, et s'écarter.

## Licence

[MIT](LICENSE). Sans affiliation ni approbation d'Anthropic.
