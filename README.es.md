# AfterLimit

**Tu agente de codificación con IA alcanzó su límite de uso. AfterLimit retoma el trabajo en el instante en que el límite se restablece — mientras duermes, almuerzas o estás fuera todo el fin de semana.**

[English](README.en.md) · [한국어](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

Ya has visto esto. Tu agente se detiene a mitad de tarea a las 2 p. m., el límite se restablece a las 7 p. m., y esas cinco horas simplemente… se pierden — a menos que estés frente a tu terminal a las 7 para escribir «continue».

AfterLimit cierra esa brecha. Un pequeño trabajo en segundo plano detecta el restablecimiento y reanuda automáticamente tus sesiones en pausa. Vuelves y encuentras trabajo terminado, no un prompt detenido.

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← el límite ya se levantó
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← faltan 5 h 18 min
```

## Por qué es diferente

**Vigilar no cuesta tokens.** AfterLimit lee tus registros de sesión locales — nunca llama al modelo para comprobar tu estado. Los tokens solo se gastan en el instante en que el trabajo se reanuda de verdad. Sondear cada 5 minutos es gratis.

**No burla el límite.** AfterLimit espera el restablecimiento *real* que informó la API y reanuda después. Nunca lo elude, lo falsifica ni martillea el endpoint. Si el límite aún no se levantó, se retira y vuelve a mirar.

**Reanuda el contexto, no un prompt nuevo.** Ejecuta `claude --resume <session>`, de modo que el agente continúa con su lista de tareas en curso y el estado de los archivos intactos — no un arranque en frío que olvidó lo que hacía.

**Correcto en cualquier zona horaria.** La hora de restablecimiento que muestra Claude («resets 11pm») no tiene zona horaria — es tu hora local de reloj de pared. AfterLimit la ancla a la zona horaria de la máquina, así que se lee bien en Seúl, Nueva York o Berlín. (Es un error real en implementaciones ingenuas: fijar una zona horaria envía a cada usuario fuera de ella a la hora equivocada.)

## Instalación

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

El instalador detecta tu sistema operativo y registra un trabajo en segundo plano que se ejecuta cada 5 minutos:

- **macOS** → un LaunchAgent de `launchd`
- **Linux** → un temporizador `systemd --user` (recurre a una línea de `cron` si no hay systemd)

Luego comprueba que ve tus sesiones:

```bash
afterlimit scan     # qué está bloqueado y cuándo se levanta — no ejecuta nada
afterlimit config   # dónde busca, tu zona horaria, notificaciones
```

Requisitos: Python 3.11+ y el CLI `claude` en tu `PATH`. **Cero dependencias en tiempo de ejecución** — solo biblioteca estándar.

## Cómo funciona

```
cada 5 min ──► escanea ~/.claude/projects/*.jsonl
                 │
                 ├─ ¿el último mensaje es un error de límite de uso?  ── no ─► omitir
                 ├─ ¿hora de restablecimiento leída y ya pasó?         ── no ─► esperar
                 ├─ ¿ya se reanudó dentro del enfriamiento?            ── sí ─► omitir
                 │
                 └─► claude --resume <session>  ──►  notificar (webhook opcional)
```

Cada salvaguarda existe para responder la pregunta que harán los jueces — *«¿esto no es solo spamear al modelo?»* No:

| Salvaguarda | Lo que evita |
|---|---|
| El último mensaje debe ser un error de API | Reanudar una sesión que ya avanzó |
| La hora de restablecimiento debe haber pasado | Tocar antes de que el límite se levante de verdad |
| Una reanudación por ciclo (configurable) | Que un cúmulo de sesiones bloqueadas se dispare a la vez |
| Enfriamiento de 5 h por sesión | Reanudar la misma sesión una y otra vez |
| Bloqueo de instancia única | Que ejecuciones solapadas del planificador disparen dos veces |
| Límite de antigüedad (3 días) | Revivir trabajo muerto y quemar tokens |

## Configuración

Funciona sin configuración. Para cambiar algo, coloca un `config.json` en `~/.config/afterlimit/`:

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

Si el directorio de sesiones de Codex no existe (no está instalado), se omite en silencio — no hay razón para condicionarlo. Pon `enable_codex` en `false` para vigilar solo Claude Code (comportamiento anterior a 0.3.0).

Las notificaciones van a cualquier webhook que acepte JSON — Slack, Discord o tu propio endpoint (el formato del payload se elige según la URL). Sin webhook, no hay notificaciones; nada más cambia. También puedes definir `AFTERLIMIT_WEBHOOK_URL` en el entorno.

Prueba antes de actuar:

```bash
afterlimit --dry-run run    # muestra qué reanudaría, sin ejecutar nada
```

## Desinstalación

```bash
./install.sh --uninstall
```

Elimina el trabajo en segundo plano y el CLI. Tus archivos de estado permanecen en `~/.local/state/afterlimit` hasta que los borres.

## Alcance y hoja de ruta

AfterLimit reanuda sesiones **headless** — el agente no necesita estar en ejecución; lee los registros y continúa el trabajo. Es deliberadamente independiente del editor y del terminal: funciona ya sea que manejes Claude Code desde un terminal simple, VS Code o cualquier otro sitio.

**Vigila tanto Claude Code como Codex CLI.** Escanea los registros de sesión que existan, así que usar solo uno o ambos funciona igual. Codex reporta el tipo de error mediante un campo estructurado (`codex_error_info`) en vez de texto libre — no hace falta adivinar con regex. Las versiones de Codex CLI tampoco coinciden en la forma del log (las versiones antiguas registran el error como un evento independiente; las nuevas lo anidan dentro de un evento `task_complete`) — se soportan ambas.

Lo que aún no cubre, dicho con honestidad:

- **Reanudación en TUI interactiva** — pulsar «continue» dentro de un panel tmux *vivo* bloqueado a mitad de conversación. Un prototipo anterior lo hacía; es solo para tmux y frágil, así que se deja como un modo opcional futuro en vez de publicarlo a medias.
- **Otros agentes** — Claude Code y Codex ya están cubiertos. Se aceptan adaptadores para otros CLI; el núcleo de análisis del límite en sí es independiente del agente.
- **Planificador de Windows** — `install.ps1` registra una tarea en el Task Scheduler; la CLI y las pruebas corren en Windows en CI. Lo único que CI no puede probar es que el Task Scheduler realmente *se dispare* en un escritorio real — verifícalo con `Get-ScheduledTask AfterLimit`.

## Notas de diseño

- **Cero dependencias en tiempo de ejecución.** Solo biblioteca estándar — nada que auditar, nada que se rompa al instalar, sin enredos de licencia.
- **Núcleo puro, con pruebas.** El análisis del límite y el escaneo de sesiones son funciones puras sin E/S, verificadas de forma cruzada en Seúl / Nueva York / UTC / Berlín para que la lógica de zona horaria no pueda regresar en silencio. `pytest -q`.
- **El núcleo hace una sola cosa.** Detecta el restablecimiento, reanuda una vez y se aparta.

## Licencia

[MIT](LICENSE). Sin afiliación ni respaldo de Anthropic.
