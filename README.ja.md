# AfterLimit

**AI コーディングエージェントが使用量上限で止まりました。AfterLimit は上限がリセットされたその瞬間に作業を引き継ぎます——あなたが寝ている間も、昼食に出ている間も、週末ずっと不在でも。**

[English](README.en.md) · [한국어](README.md) · [中文](README.zh.md) · **日本語** · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

見覚えのある画面です。午後 2 時に作業が止まり、上限は 7 時にリセットされ、その 5 時間はただ消えます——ちょうど 7 時に端末の前に座って「continue」と打つのでなければ。

AfterLimit はその空白を埋めます。小さなバックグラウンドジョブがリセットに気づき、止まっていたセッションを自動で続行します。戻ってくると、止まったプロンプトではなく完了した作業が待っています。

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← 上限はすでに解除
  [waiting]  docs-site/32d57b  usage   resets 19:50   ← あと 5 時間 18 分
```

## 何が違うのか

**監視にトークンはかかりません。** AfterLimit はローカルのセッションログを読むだけで、あなたを確認するためにモデルを呼び出しません。トークンは作業が実際に再開する瞬間にのみ消費されます。5 分ごとに覗くこと自体は無料です。

**上限を小細工で回避しません。** AfterLimit は API が報告した*本当の*リセット時刻を待ち、その後に再開します。回避したり、偽装したり、エンドポイントを叩き続けたりしません。まだ解除されていなければ、退いて後で見直します。

**新規プロンプトではなく文脈を引き継ぎます。** `claude --resume <session>` で実行するため、進行中の TODO リストとファイル状態をそのままに続行します——何をしていたか忘れたコールドスタートではありません。

**どこでもタイムゾーンが正確です。** Claude が表示するリセット時刻（「resets 11pm」）にはタイムゾーンがありません——あなたのローカルの壁時計時刻です。AfterLimit はこれを端末のタイムゾーンに合わせて解釈するため、ソウルでもニューヨークでもベルリンでも正しく読めます。（素朴な実装で実際に起きるバグです。タイムゾーンを固定すると、その地域外のユーザー全員が誤った時刻に再開されます。）

## インストール

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

インストーラは OS を検出し、5 分ごとに実行するバックグラウンドジョブを登録します:

- **macOS** → `launchd` LaunchAgent
- **Linux** → `systemd --user` タイマー（systemd が無ければ `cron` 一行にフォールバック）

その後、セッションが見えているか確認します:

```bash
afterlimit scan     # 何がブロックされ、いつ解除されるか——何も実行しない
afterlimit config   # どこを見ているか、タイムゾーン、通知設定
```

要件: Python 3.11 以上、`claude` CLI が `PATH` にあること。**ランタイム依存ゼロ**——標準ライブラリのみ。

## しくみ

```
5 分ごと ──► ~/.claude/projects/*.jsonl をスキャン
              │
              ├─ 最後のメッセージが使用量上限エラーか？    ── いいえ ─► スキップ
              ├─ リセット時刻を解析済みで、もう過ぎたか？  ── いいえ ─► 待機
              ├─ クールダウン中にすでに再開したか？        ── はい ─► スキップ
              │
              └─► claude --resume <session>  ──►  通知（任意の webhook）
```

すべてのガードは、審査員が必ず問う一点——*「これは結局モデルを連打しているだけでは？」*——に答えるためにあります。違います:

| ガード | 防ぐこと |
|---|---|
| 最後のメッセージが API エラーであること | すでに進行済みのセッションを再開する |
| リセット時刻を過ぎていること | 上限が本当に解除される前に叩く |
| サイクルあたり 1 回の再開（設定可） | ブロック済みセッションが一斉に発火する |
| セッションごと 5 時間のクールダウン | 同じセッションを繰り返し再開する |
| 単一インスタンスロック | スケジューラの重複実行による二重発火 |
| セッション年齢上限（3 日） | 死んだバックログを蘇らせトークンを浪費する |

## 設定

デフォルトで動作します。変更するには `~/.config/afterlimit/config.json` に置きます:

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

Codex のセッションディレクトリが存在しなければ（未インストールなら）静かにスキップします——それをゲートする理由はありません。`enable_codex` を `false` にすると Claude Code だけを監視します（0.3.0 以前の動作）。

通知は JSON を受け取る webhook ならどこへでも送れます——Slack、Discord、または独自のエンドポイント（ペイロード形式は URL から選択）。webhook が無ければ通知も無く、それ以外は何も変わりません。環境変数 `AFTERLIMIT_WEBHOOK_URL` でも設定できます。

実行前にプレビュー:

```bash
afterlimit --dry-run run    # 何を再開するかだけ表示し、実行はしない
```

## アンインストール

```bash
./install.sh --uninstall
```

バックグラウンドジョブと CLI を削除します。状態ファイルは削除するまで `~/.local/state/afterlimit` に残ります。

## 範囲とロードマップ

AfterLimit は**ヘッドレス**セッションを再開します——エージェントが動いている必要はなく、ログを読んで作業を続けます。これは意図的にエディタ・端末非依存です。Claude Code を素の端末で動かしても、VS Code で動かしても、どこでも動作します。

**Claude Code と Codex CLI の両方を監視します。**存在するセッションログをスキャンするだけなので、片方だけでも両方でも同じように動作します。Codex はエラーの種類を自由文ではなく構造化フィールド（`codex_error_info`）で伝えるので、正規表現で推測する必要がありません。Codex CLI のバージョンによってログの形も違います（旧バージョンはエラーを独立イベントとして記録し、新バージョンは `task_complete` イベントの中に入れ子にします）——両方に対応しています。

まだ扱っていないこと、正直に記します:

- **対話型 TUI 再開**——*動作中*の tmux ペインで会話中にブロックされたときに「continue」を押すこと。以前のプロトタイプはこれを行いましたが、tmux 専用で壊れやすいため、中途半端に出荷せず将来のオプトインモードとして残します。
- **他のエージェント** — Claude Code と Codex はカバー済みです。他の CLI 用アダプタの貢献を歓迎します——上限解析のコア自体はエージェント非依存です。
- **Windows スケジューラ** — `install.ps1` が Task Scheduler ジョブを登録します。CLI とテストは CI 上の Windows でも動きます。CI が証明できない唯一のことは、実機デスクトップで Task Scheduler が実際に*発火する*ことです——`Get-ScheduledTask AfterLimit` で確認してください。

## 設計ノート

- **ランタイム依存ゼロ。** 標準ライブラリのみ——監査すべき依存も、インストール時に壊れるものも、ライセンスの絡みもありません。
- **純粋なコア、テスト済み。** 上限解析とセッションスキャンは I/O のない純関数で、ソウル／ニューヨーク／UTC／ベルリンで交差検証し、タイムゾーンロジックが静かに退行しないようにしています。`pytest -q`。
- **コアは一つのことだけ行います。** リセットを検出し、一度再開し、退きます。

## ライセンス

[MIT](LICENSE)。Anthropic とは無関係であり、承認も受けていません。
