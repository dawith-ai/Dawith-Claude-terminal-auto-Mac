# AfterLimit

**Seu agente de codificação com IA atingiu o limite de uso. O AfterLimit retoma o trabalho no instante em que o limite é redefinido — enquanto você dorme, almoça ou está fora o fim de semana inteiro.**

[English](README.en.md) · [한국어](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Português** · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

Você já viu isto. Seu agente para no meio da tarefa às 14h, o limite é redefinido às 19h, e essas cinco horas simplesmente… se perdem — a menos que você esteja na frente do terminal às 19h para digitar «continue».

O AfterLimit fecha essa lacuna. Um pequeno trabalho em segundo plano percebe a redefinição e retoma automaticamente suas sessões em pausa. Você volta para o trabalho concluído, não para um prompt travado.

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← limite já liberado
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← faltam 5h 18min
```

## Por que é diferente

**Vigiar não custa tokens.** O AfterLimit lê seus registros de sessão locais — ele nunca chama o modelo para verificar você. Tokens só são gastos no instante em que o trabalho realmente é retomado. Consultar a cada 5 minutos é grátis.

**Ele não burla o limite com truques.** O AfterLimit espera a redefinição *real* informada pela API e retoma depois. Ele nunca contorna, falsifica nem martela o endpoint. Se o limite ainda não foi liberado, ele recua e verifica de novo.

**Ele retoma o contexto, não um prompt novo.** Executa `claude --resume <session>`, de modo que o agente continua com sua lista de tarefas em andamento e o estado dos arquivos intactos — não uma partida a frio que esqueceu o que estava fazendo.

**Correto em qualquer fuso horário.** A hora de redefinição que o Claude mostra («resets 11pm») não tem fuso — é a sua hora local de relógio de parede. O AfterLimit a ancora ao fuso da máquina, então ela é lida corretamente em Seul, Nova York ou Berlim. (É um bug real de implementações ingênuas: fixar um fuso envia cada usuário fora dele para a hora errada.)

## Instalação

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

O instalador detecta seu sistema operacional e registra um trabalho em segundo plano que roda a cada 5 minutos:

- **macOS** → um LaunchAgent do `launchd`
- **Linux** → um timer `systemd --user` (recorre a uma linha `cron` se não houver systemd)

Depois verifique se ele enxerga suas sessões:

```bash
afterlimit scan     # o que está bloqueado e quando libera — não executa nada
afterlimit config   # onde ele procura, seu fuso, notificações
```

Requisitos: Python 3.11+ e o CLI `claude` no seu `PATH`. **Zero dependências em tempo de execução** — apenas biblioteca padrão.

## Como funciona

```
a cada 5 min ──► varre ~/.claude/projects/*.jsonl
                   │
                   ├─ a última mensagem é um erro de limite?          ── não ─► pular
                   ├─ hora de redefinição lida e já passou?           ── não ─► esperar
                   ├─ já retomou dentro do resfriamento?              ── sim ─► pular
                   │
                   └─► claude --resume <session>  ──►  notificar (webhook opcional)
```

Cada proteção existe para responder à pergunta que os jurados farão — *«isso não é só spam para o modelo?»* Não:

| Proteção | O que evita |
|---|---|
| A última mensagem deve ser um erro de API | Retomar uma sessão que já avançou |
| A hora de redefinição deve ter passado | Bater antes de o limite realmente liberar |
| Uma retomada por ciclo (configurável) | Que um monte de sessões bloqueadas dispare de uma vez |
| Resfriamento de 5h por sessão | Retomar a mesma sessão repetidamente |
| Trava de instância única | Que execuções sobrepostas do agendador disparem em dobro |
| Limite de idade (3 dias) | Reviver pendências mortas e queimar tokens |

## Configuração

Funciona sem configuração. Para mudar, coloque um `config.json` em `~/.config/afterlimit/`:

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

Se o diretório de sessões do Codex não existir (não instalado), é ignorado silenciosamente — não há motivo para condicionar isso. Defina `enable_codex` como `false` para observar somente o Claude Code (comportamento anterior à 0.3.0).

As notificações vão para qualquer webhook que aceite JSON — Slack, Discord ou seu próprio endpoint (o formato do payload é escolhido pela URL). Sem webhook, sem notificações; nada mais muda. Você também pode definir `AFTERLIMIT_WEBHOOK_URL` no ambiente.

Pré-visualize antes de agir:

```bash
afterlimit --dry-run run    # mostra o que retomaria, sem executar nada
```

## Desinstalação

```bash
./install.sh --uninstall
```

Remove o trabalho em segundo plano e o CLI. Seus arquivos de estado permanecem em `~/.local/state/afterlimit` até você apagá-los.

## Escopo e roteiro

O AfterLimit retoma sessões **headless** — o agente não precisa estar em execução; ele lê os registros e continua o trabalho. Isso é deliberadamente independente de editor e terminal: funciona quer você use o Claude Code de um terminal simples, do VS Code ou de qualquer outro lugar.

**Observa tanto o Claude Code quanto o Codex CLI.** Ele varre os registros de sessão que existirem, então usar apenas um ou os dois funciona do mesmo jeito. O Codex informa o tipo de erro por um campo estruturado (`codex_error_info`) em vez de texto livre — sem precisar adivinhar com regex. As versões do Codex CLI também divergem no formato do log (versões antigas registram o erro como um evento independente; versões novas o aninham dentro de um evento `task_complete`) — ambas são suportadas.

O que ainda não é coberto, dito com honestidade:

- **Retomada em TUI interativa** — pressionar «continue» dentro de um painel tmux *ativo* bloqueado no meio da conversa. Um protótipo anterior fazia isso; é só para tmux e frágil, então fica como um modo opcional futuro em vez de ser publicado pela metade.
- **Outros agentes** — Claude Code e Codex já estão cobertos. Adaptadores para outros CLIs são bem-vindos; o núcleo de análise do limite em si é independente do agente.
- **Agendador do Windows** — `install.ps1` registra uma tarefa no Task Scheduler; a CLI e os testes rodam no Windows na CI. A única coisa que a CI não consegue provar é o Task Scheduler realmente *disparar* num desktop real — verifique com `Get-ScheduledTask AfterLimit`.

## Notas de design

- **Zero dependências em tempo de execução.** Apenas biblioteca padrão — nada para auditar, nada que quebre na instalação, sem enredos de licença.
- **Núcleo puro, testado.** A análise do limite e a varredura de sessões são funções puras sem E/S, verificadas de forma cruzada em Seul / Nova York / UTC / Berlim para que a lógica de fuso não regrida em silêncio. `pytest -q`.
- **O núcleo faz uma coisa.** Detectar a redefinição, retomar uma vez e sair do caminho.

## Licença

[MIT](LICENSE). Sem afiliação ou endosso da Anthropic.
