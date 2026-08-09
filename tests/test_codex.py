"""Codex CLI 지원 — 파싱·스캔·재개 명령.

실제 세션 로그(`~/.codex/sessions/**/rollout-*.jsonl`)와 라이브 재현에서 뽑은
문구를 그대로 픽스처로 쓴다(2026-08-09 확보). Codex 는 Claude 와 달리 오류
종류를 구조화된 `codex_error_info` 필드로 알려준다 — 텍스트로 추측하지 않는다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from afterlimit.config import Config
from afterlimit.limits import parse_codex_limit
from afterlimit.resume import _fresh_cmd, _resume_cmd
from afterlimit.sessions import BlockedSession, scan_blocked

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 9, 14, 0, tzinfo=SEOUL)

#: 실측(2026-08-09, 라이브 재현 + 과거 세션 로그)
USAGE_TIME_ONLY = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits "
    "or try again at 12:09 AM."
)
USAGE_DATED = (
    "You've hit your usage limit. Upgrade to Plus to continue using Codex "
    "(https://chatgpt.com/explore/plus), or try again at Sep 5th, 2026 12:39 AM."
)
UNAUTHORIZED = (
    "Your access token could not be refreshed because you have since logged out "
    "or signed in to another account. Please sign in again."
)


# ── parse_codex_limit ────────────────────────────────────────────────────

def test_시간만_있는_reset을_읽는다():
    anchor = NOW
    r = parse_codex_limit(USAGE_TIME_ONLY, anchor=anchor, now=anchor)
    assert r.kind == "codex"
    assert r.reset_at is not None
    assert (r.reset_at.hour, r.reset_at.minute) == (0, 9)  # 12:09 AM


def test_연도까지_있는_reset을_읽는다():
    anchor = NOW
    r = parse_codex_limit(USAGE_DATED, anchor=anchor, now=anchor)
    assert r.reset_at == datetime(2026, 9, 5, 0, 39, tzinfo=SEOUL)


def test_인증만료_문구는_한도가_아니다():
    """'usage limit' 문구가 없다 — _inspect_codex 가 codex_error_info 로 따로 잡는다."""
    assert parse_codex_limit(UNAUTHORIZED, anchor=NOW, now=NOW) is None


def test_anchor가_naive면_거부한다():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        parse_codex_limit(USAGE_TIME_ONLY, anchor=datetime(2026, 8, 9))


# ── 세션 스캔 ─────────────────────────────────────────────────────────────

def _write_codex_session(tmp_path, *, sid, cwd, last_payload, mtime=NOW):
    d = tmp_path / "codex" / "2026" / "08" / "09"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-08-09T00-00-00-{sid}.jsonl"
    meta = json.dumps(
        {
            "timestamp": mtime.isoformat(),
            "type": "session_meta",
            "payload": {
                "session_id": sid,
                "cwd": cwd,
                "timestamp": (mtime - timedelta(hours=1)).isoformat(),
                "originator": "codex_exec",
            },
        },
        ensure_ascii=False,
    )
    lines = [meta]
    if last_payload is not None:
        lines.append(json.dumps({"type": "event_msg", "payload": last_payload}, ensure_ascii=False))
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ts = mtime.timestamp()
    os.utime(f, (ts, ts))
    return f


def _cfg(tmp_path):
    return Config(
        projects_dir=tmp_path / "claude_없음",  # Claude 쪽은 이 테스트의 관심사가 아니다
        state_dir=tmp_path / "state",
        codex_sessions_dir=tmp_path / "codex",
    )


def test_사용량_한도로_막힌_codex_세션을_찾는다(tmp_path):
    _write_codex_session(
        tmp_path, sid="aaaaaaaa-1111-2222-3333-444444444444", cwd="/work/repo",
        last_payload={"type": "error", "codex_error_info": "usage_limit_exceeded",
                      "message": USAGE_TIME_ONLY},
    )
    found = scan_blocked(_cfg(tmp_path), now=NOW)
    assert len(found) == 1
    s = found[0]
    assert s.provider == "codex"
    assert s.limit.kind == "codex"
    assert s.limit.reset_at is not None
    assert s.cwd == "/work/repo"
    assert s.session_id == "aaaaaaaa-1111-2222-3333-444444444444"
    assert s.project == "repo"  # cwd 이름 — 날짜 폴더명이 아니다


def test_신버전_codex는_오류를_task_complete_안에_넣는다(tmp_path):
    """실측(2026-08-09): 이 기기의 8월 로그 189건이 전부 이 형태였다 — 예전
    (독립 error 이벤트) 형태는 하나도 없었다. 실제 라이브 재현 파일 그대로.
    """
    _write_codex_session(
        tmp_path, sid="019fe42c-94d2-7813-81e9-55c4cf434cac", cwd="/Users/dawith/orca/openclaw",
        last_payload={
            "type": "task_complete", "turn_id": "019fe42c-9550-7b90-8926-7b04570ee6c5",
            "last_agent_message": None,
            "error": {"message": USAGE_DATED, "codex_error_info": "usage_limit_exceeded"},
            "started_at": 1786239554, "completed_at": 1786239559, "duration_ms": 4229,
        },
    )
    found = scan_blocked(_cfg(tmp_path), now=NOW)
    assert len(found) == 1
    assert found[0].limit.reset_at == datetime(2026, 9, 5, 0, 39, tzinfo=SEOUL)


def test_task_complete가_성공이면_안_막힌_것이다(tmp_path):
    """error 가 없는 정상 완료는 오탐이 아니어야 한다."""
    _write_codex_session(
        tmp_path, sid="019fe999-0000-0000-0000-000000000000", cwd="/work/repo",
        last_payload={
            "type": "task_complete", "turn_id": "x",
            "last_agent_message": "다 했습니다", "error": None,
        },
    )
    assert scan_blocked(_cfg(tmp_path), now=NOW) == []


def test_인증_만료로_막힌_codex_세션도_잡는다(tmp_path):
    """해제 시각은 없다 — 백오프가 속도를 정한다."""
    _write_codex_session(
        tmp_path, sid="bbbbbbbb-1111-2222-3333-444444444444", cwd="/work/repo",
        last_payload={"type": "error", "codex_error_info": "unauthorized", "message": UNAUTHORIZED},
    )
    found = scan_blocked(_cfg(tmp_path), now=NOW)
    assert len(found) == 1
    assert found[0].limit.reset_at is None


def test_오류_뒤에_더_진행됐으면_막힌_게_아니다(tmp_path):
    _write_codex_session(
        tmp_path, sid="cccccccc-1111-2222-3333-444444444444", cwd="/work/repo",
        last_payload={"type": "agent_message", "message": "다 끝냈습니다"},
    )
    assert scan_blocked(_cfg(tmp_path), now=NOW) == []


def test_cwd나_session_id를_못_읽으면_건너뛴다(tmp_path):
    d = tmp_path / "codex" / "2026" / "08" / "09"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "rollout-2026-08-09T00-00-00-broken.jsonl"
    f.write_text("이건 JSON 이 아니다\n", encoding="utf-8")
    ts = NOW.timestamp()
    os.utime(f, (ts, ts))
    assert scan_blocked(_cfg(tmp_path), now=NOW) == []


def test_enable_codex가_꺼져있으면_건너뛴다(tmp_path):
    _write_codex_session(
        tmp_path, sid="dddddddd-1111-2222-3333-444444444444", cwd="/work/repo",
        last_payload={"type": "error", "codex_error_info": "usage_limit_exceeded",
                      "message": USAGE_TIME_ONLY},
    )
    from dataclasses import replace

    cfg = replace(_cfg(tmp_path), enable_codex=False)
    assert scan_blocked(cfg, now=NOW) == []


# ── 재개 명령 ─────────────────────────────────────────────────────────────

def _codex_session():
    return BlockedSession(
        session_id="eeeeeeee-1111-2222-3333-444444444444",
        jsonl=None, cwd="/work/repo",
        limit=None,  # 이 테스트에서는 안 씀
        blocked_at=NOW, started_at=NOW,
        provider="codex",
    )


def test_codex_재개_명령을_만든다():
    cfg = Config()
    cmd = _resume_cmd(_codex_session(), cfg, "이어서 하세요")
    assert cmd[:3] == ["codex", "exec", "resume"]
    assert "eeeeeeee-1111-2222-3333-444444444444" in cmd
    assert "이어서 하세요" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_codex_새로_시작_명령을_만든다():
    cfg = Config()
    cmd = _fresh_cmd(_codex_session(), cfg, "새로 시작")
    assert cmd[:2] == ["codex", "exec"]
    assert "resume" not in cmd  # 새로 시작이니 resume 서브커맨드를 안 쓴다
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_claude_세션은_그대로_claude_명령을_쓴다():
    """provider 기본값이 'claude' 라 기존 동작이 그대로 보존된다."""
    cfg = Config()
    s = BlockedSession(
        session_id="ffffffff-1111-2222-3333-444444444444",
        jsonl=None, cwd="/work/repo", limit=None,
        blocked_at=NOW, started_at=NOW,
    )
    cmd = _resume_cmd(s, cfg, "이어서")
    assert cmd[0] == "claude"
    assert "--resume" in cmd
