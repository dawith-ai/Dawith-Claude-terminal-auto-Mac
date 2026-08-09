# AfterLimit

**你的 AI 编码代理触及了用量上限。AfterLimit 会在上限重置的那一刻接手继续工作——无论你在睡觉、吃午饭，还是整个周末都不在。**

[English](README.en.md) · [한국어](README.md) · **中文** · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

你见过这个提示。代理在下午 2 点中途停下，上限在 7 点重置，而那 5 个小时就这么没了——除非你恰好 7 点坐在终端前敲下 “continue”。

AfterLimit 填补了这段空白。一个小小的后台任务会察觉重置，并自动继续你被搁置的会话。你回来时看到的是完成的工作，而不是卡住的提示符。

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← 上限已解除
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← 还剩 5 小时 18 分
```

## 有何不同

**监视不消耗令牌。** AfterLimit 只读取本地会话日志——它绝不调用模型来检查你的状态。令牌只在工作真正恢复的那一刻消耗。每 5 分钟查看一次本身是免费的。

**它不投机取巧地绕过上限。** AfterLimit 等待 API 报告的*真实*重置时间，之后才恢复。它绝不绕过、伪造或反复冲击接口。若上限尚未解除，它会退避并稍后再看。

**它恢复的是上下文，而非全新提示。** 它运行 `claude --resume <session>`，让代理带着进行中的待办清单和文件状态继续——而不是忘了自己在做什么的冷启动。

**在任何时区都正确。** Claude 显示的重置时间（“resets 11pm”）不带时区——那是你的本地墙钟时间。AfterLimit 将其锚定到本机时区，因此在首尔、纽约或柏林都能正确解读。（这是幼稚实现中真实存在的 bug：把时区写死会让该地区之外的每位用户都在错误的时间被恢复。）

## 安装

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

安装脚本会检测你的操作系统，并注册一个每 5 分钟运行一次的后台任务：

- **macOS** → `launchd` LaunchAgent
- **Linux** → `systemd --user` 定时器（若无 systemd，则回退为一行 `cron`）

然后确认它能看到你的会话：

```bash
afterlimit scan     # 什么被阻塞、何时解除——不执行任何操作
afterlimit config   # 它在看哪里、你的时区、通知设置
```

要求：Python 3.11+，且 `claude` CLI 在 `PATH` 中。**零运行时依赖**——仅标准库。

## 工作原理

```
每 5 分钟 ──► 扫描 ~/.claude/projects/*.jsonl
               │
               ├─ 最后一条消息是用量上限错误吗？        ── 否 ─► 跳过
               ├─ 已解析出重置时间且已过？              ── 否 ─► 等待
               ├─ 在冷却窗口内已经恢复过？              ── 是 ─► 跳过
               │
               └─► claude --resume <session>  ──►  通知（可选 webhook）
```

每一道防护都为回答评委必问的一个问题——*“这不就是在刷屏调用模型吗？”* 不是：

| 防护 | 防止 |
|---|---|
| 最后一条消息必须是 API 错误 | 恢复一个已经继续过的会话 |
| 重置时间必须已过 | 在上限真正解除前就去敲门 |
| 每周期一次恢复（可配置） | 一堆被阻塞的会话同时触发 |
| 每会话 5 小时冷却 | 反复恢复同一个会话 |
| 单实例锁 | 调度器重叠运行导致重复触发 |
| 会话年龄上限（3 天） | 复活早已死去的积压、白白烧令牌 |

## 配置

默认无需配置即可运行。要修改，就在 `~/.config/afterlimit/config.json` 放入：

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

如果 Codex 会话目录不存在（未安装），会静默跳过——没理由为此设开关。将 `enable_codex` 设为 `false` 可只监视 Claude Code（0.3.0 之前的行为）。

通知可发往任何接受 JSON 的 webhook——Slack、Discord 或你自己的端点（负载格式按 URL 选择）。没有 webhook 就没有通知，其余一切不变。也可用环境变量 `AFTERLIMIT_WEBHOOK_URL`。

行动前先预览：

```bash
afterlimit --dry-run run    # 只显示会恢复什么，不执行
```

## 卸载

```bash
./install.sh --uninstall
```

移除后台任务和 CLI。状态文件会保留在 `~/.local/state/afterlimit`，直到你删除。

## 范围与路线图

AfterLimit 恢复的是**无头（headless）**会话——代理无需运行；它读取日志并继续工作。这刻意做到不依赖编辑器与终端：无论你用普通终端、VS Code 还是其他方式驱动 Claude Code，都能工作。

**同时关注 Claude Code 与 Codex CLI。**它只扫描存在的会话日志，所以只用其中一个或两个都用，行为一致。Codex 通过结构化字段（`codex_error_info`）报告错误类型，而不是自由文本——不需要用正则猜测。Codex CLI 各版本的日志结构也不一致（旧版本把错误记为独立事件；新版本把错误嵌套在 `task_complete` 事件里）——两种都支持。

尚未处理的，诚实列出：

- **交互式 TUI 恢复**——在*运行中*的 tmux 窗格里、对话中途被阻塞时按下 “continue”。早期原型做过这个；它仅限 tmux 且脆弱，因此留作未来的可选模式，而非半成品发布。
- **其他代理** — Claude Code 与 Codex 均已覆盖。欢迎为其他 CLI 贡献适配器；上限解析核心本身与代理无关。
- **Windows 调度器** — `install.ps1` 会注册一个 Task Scheduler 任务；CLI 和测试在 CI 里的 Windows 上运行。CI 唯一无法证明的是 Task Scheduler 在真实桌面上*触发*——用 `Get-ScheduledTask AfterLimit` 自行验证。

## 设计说明

- **零运行时依赖。** 仅标准库——没有要审计的依赖，没有会在安装时损坏的东西，没有许可证纠缠。
- **纯核心，有测试。** 上限解析与会话扫描是无 I/O 的纯函数，跨首尔／纽约／UTC／柏林交叉验证，使时区逻辑无法悄悄回退。`pytest -q`。
- **核心只做一件事。** 检测重置、恢复一次、随即退场。

## 许可证

[MIT](LICENSE)。与 Anthropic 无隶属关系，也未获其背书。
