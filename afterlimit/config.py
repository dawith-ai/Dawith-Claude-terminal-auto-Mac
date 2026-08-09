"""설정 — 개인 환경에 의존하지 않는 기본값 + 파일/환경변수 덮어쓰기.

경로를 코드에 박지 않는다. macOS 와 Linux 모두에서 같은 규칙으로 동작한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = ["Config", "DEFAULT_RESUME_PROMPT", "config_path", "state_dir"]

#: 재개할 때 에이전트에게 보낼 지시. 프로젝트마다 다르므로 설정으로 바꿀 수 있다.
#: 기본값은 특정 파일명이나 언어를 전제하지 않는다.
DEFAULT_RESUME_PROMPT = (
    "Your usage limit has just reset. Continue the work that was in progress. "
    "Do not ask follow-up questions; proceed with whatever you can do now. "
    "Work in small increments and commit your changes so the next session can pick up cleanly. "
    "Do not run destructive commands (rm -rf, git push --force, git reset --hard, "
    "DROP/DELETE statements, or edits to secrets) without explicit confirmation. "
    "When you stop, state in one line what you finished and what comes next."
)


def _xdg(var: str, fallback: str) -> Path:
    """XDG 규칙. macOS 에도 그대로 적용해 두 OS 의 경로 규칙을 하나로 유지한다."""
    raw = os.environ.get(var)
    return Path(raw) if raw else Path.home() / fallback


def state_dir() -> Path:
    """상태·로그·잠금 파일이 사는 곳. /tmp 를 쓰지 않는다 — 재부팅 시 사라지고 충돌한다."""
    return _xdg("XDG_STATE_HOME", ".local/state") / "afterlimit"


def config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "afterlimit" / "config.json"


def _codex_home() -> Path:
    """Codex CLI 자신과 같은 규칙. `CODEX_HOME` 을 존중한다 — 그래야 사용자가 커스텀
    위치를 썼을 때도 같은 곳을 본다."""
    raw = os.environ.get("CODEX_HOME")
    return Path(raw) if raw else Path.home() / ".codex"


@dataclass(frozen=True)
class Config:
    #: Claude Code 가 세션 기록을 쌓는 곳
    projects_dir: Path = field(default_factory=lambda: Path.home() / ".claude" / "projects")
    state_dir: Path = field(default_factory=state_dir)

    #: Codex CLI 가 세션 기록을 쌓는 곳. 디렉터리가 없으면 조용히 건너뛴다(설치 안 한 사람도 많다).
    codex_sessions_dir: Path = field(default_factory=lambda: _codex_home() / "sessions")
    codex_bin: str = "codex"
    #: Codex 도 함께 감시할지. 끄면 Claude Code 만 본다(0.2.x 이전 동작).
    enable_codex: bool = True

    #: 이 시간 안에 활동이 있던 세션만 본다
    active_within_hours: int = 12
    #: 세션이 처음 만들어진 지 이만큼 지났으면 죽은 백로그로 보고 건드리지 않는다
    max_session_age_days: int = 3
    #: 한 사이클에 재개할 세션 수. 과하게 두드리지 않기 위한 상한
    max_resume_per_cycle: int = 1
    #: 같은 세션을 다시 재개하기까지의 최소 간격
    resume_cooldown_hours: int = 5
    #: 재개 1회의 최대 실행 시간
    invoke_timeout_sec: int = 900

    claude_bin: str = "claude"
    resume_prompt: str = DEFAULT_RESUME_PROMPT

    #: 알림 웹훅. Discord/Slack/그 외 무엇이든 URL 만 있으면 된다. 없으면 알리지 않는다.
    webhook_url: str | None = None
    #: True 면 실제로 재개하지 않고 무엇을 할지만 보고한다
    dry_run: bool = False

    #: 세션이 돌린 명령이 한도에 걸려 하던 일이 끊긴 채 멈춘 세션도 이어줄지.
    #: 끄면 세션 자신의 API 호출이 막힌 경우만 다룬다(0.1.0 동작).
    resume_stalled: bool = True
    #: 그 판정에 볼 꼬리 레코드 수.
    stalled_tail_records: int = 30
    #: 이 시간 이상 멈춰 있어야 대상. 사람이 지금 쓰는 세션을 뺏지 않기 위한 여유.
    stalled_idle_minutes: int = 20

    @property
    def lock_dir(self) -> Path:
        return self.state_dir / "locks"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "afterlimit.log"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """설정 파일 → 환경변수 순으로 덮어쓴다. 파일이 없어도 기본값으로 동작한다."""
        cfg = cls()
        path = path or config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                raise ValueError(f"설정 파일을 읽을 수 없습니다: {path} ({e})") from e
            cfg = cfg._apply(data)

        env = {}
        if url := os.environ.get("AFTERLIMIT_WEBHOOK_URL"):
            env["webhook_url"] = url
        if os.environ.get("AFTERLIMIT_DRY_RUN"):
            env["dry_run"] = True
        return cfg._apply(env) if env else cfg

    def _apply(self, data: dict) -> Config:
        known = {f for f in self.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"알 수 없는 설정 항목: {', '.join(sorted(unknown))}")
        coerced = {
            k: Path(v).expanduser()
            if k in ("projects_dir", "state_dir", "codex_sessions_dir")
            else v
            for k, v in data.items()
        }
        return replace(self, **coerced)
