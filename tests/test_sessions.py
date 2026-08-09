"""세션 스캔 테스트 — 특히 OS 간 이식성."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from afterlimit.config import Config
from afterlimit.sessions import BlockedSession, scan_blocked, session_started_at

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)


def _line(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _limit_msg(text="You've hit your usage limit · resets 11pm") -> str:
    return _line(
        type="assistant",
        isApiErrorMessage=True,
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


def _write_session(tmp_path, *, project="proj", session_id="abc", lines, mtime=NOW, started=None):
    d = tmp_path / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session_id}.jsonl"
    head = _line(
        type="user",
        sessionId=session_id,
        timestamp=(started or mtime).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        cwd="/work/repo",
    )
    f.write_text("\n".join([head, *lines]) + "\n", encoding="utf-8")
    ts = mtime.timestamp()
    import os

    os.utime(f, (ts, ts))
    return f


@pytest.fixture
def cfg(tmp_path):
    # codex_sessions_dir 도 tmp 밑으로 격리한다 — 안 그러면 기본값이 실제
    # ~/.codex/sessions 를 가리켜서, 이 기기의 진짜 Codex 사용 기록이 테스트에
    # 섞여 들어간다(느려지는 것은 물론, 어떤 세션이 걸리느냐에 따라 결과가
    # 흔들리는 테스트 격리 버그였다 — 실측: 스캔 시간 0.1초 → 12.8초로 급증).
    return Config(
        projects_dir=tmp_path / "projects",
        state_dir=tmp_path / "state",
        codex_sessions_dir=tmp_path / "codex",
    )


# ── 이식성 ──────────────────────────────────────────────────────────────

def test_세션_시작시각은_첫줄_타임스탬프에서_읽는다(tmp_path, cfg):
    """st_birthtime 은 macOS 전용이고, Linux 에서 mtime 으로 대신하면
    재개할 때마다 갱신돼 나이 판단이 무의미해진다. 첫 줄 timestamp 는 양쪽에서 같다."""
    started = datetime(2026, 7, 10, 9, 0, tzinfo=SEOUL)
    f = _write_session(tmp_path, lines=[_limit_msg()], mtime=NOW, started=started)

    got = session_started_at(f)
    assert got is not None
    assert got.astimezone(SEOUL) == started
    # 파일을 다시 건드려도(=재개해도) 시작 시각은 변하지 않는다
    import os

    os.utime(f, None)
    assert session_started_at(f).astimezone(SEOUL) == started


def test_오래된_백로그는_건너뛴다(tmp_path, cfg):
    _write_session(
        tmp_path,
        lines=[_limit_msg()],
        mtime=NOW,  # 방금 활동했지만
        started=NOW - timedelta(days=5),  # 5일 전에 시작된 세션
    )
    assert scan_blocked(cfg, now=NOW) == []


def test_시작시각을_모르면_나이로_거르지_않는다(tmp_path, cfg):
    d = tmp_path / "projects" / "proj"
    d.mkdir(parents=True)
    f = d / "s.jsonl"
    f.write_text("not json\n" + _limit_msg() + "\n" + _line(cwd="/work/repo") + "\n", encoding="utf-8")
    import os

    os.utime(f, (NOW.timestamp(), NOW.timestamp()))

    assert session_started_at(f) is None
    assert len(scan_blocked(cfg, now=NOW)) == 1  # 판단 불가 → 통과시킨다


# ── 막힘 판정 ───────────────────────────────────────────────────────────

def test_한도로_막힌_세션을_찾는다(tmp_path, cfg):
    _write_session(tmp_path, lines=[_limit_msg()])
    found = scan_blocked(cfg, now=NOW)

    assert len(found) == 1
    s = found[0]
    assert isinstance(s, BlockedSession)
    assert s.session_id == "abc"
    assert s.project == "proj"
    assert s.cwd == "/work/repo"
    assert s.limit.kind == "usage"
    assert s.limit.reset_at == datetime(2026, 7, 17, 23, 0, tzinfo=SEOUL)


def test_한도_뒤에_활동이_있으면_막힌게_아니다(tmp_path, cfg):
    """이미 풀려서 누군가 이어서 일한 세션 — 건드리면 안 된다."""
    _write_session(
        tmp_path,
        lines=[
            _limit_msg(),
            _line(message={"role": "user", "content": "이어서 해줘"}),
        ],
    )
    assert scan_blocked(cfg, now=NOW) == []


def test_한도를_언급하는_평범한_대화는_오탐이_아니다(tmp_path, cfg):
    """isApiErrorMessage 가 아닌 메시지는 본문에 'usage limit' 이 있어도 한도가 아니다."""
    _write_session(
        tmp_path,
        lines=[
            _line(
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "usage limit 처리 코드를 고쳤습니다"}],
                }
            )
        ],
    )
    assert scan_blocked(cfg, now=NOW) == []


def test_오래_활동이_없으면_건너뛴다(tmp_path, cfg):
    _write_session(tmp_path, lines=[_limit_msg()], mtime=NOW - timedelta(hours=20))
    assert scan_blocked(cfg, now=NOW) == []


def test_cwd가_없으면_재개할_수_없다(tmp_path, cfg):
    d = tmp_path / "projects" / "proj"
    d.mkdir(parents=True)
    f = d / "s.jsonl"
    f.write_text(_limit_msg() + "\n", encoding="utf-8")  # cwd 없음
    import os

    os.utime(f, (NOW.timestamp(), NOW.timestamp()))
    assert scan_blocked(cfg, now=NOW) == []


def test_projects_디렉터리가_없어도_죽지_않는다(tmp_path):
    cfg = Config(
        projects_dir=tmp_path / "없음",
        state_dir=tmp_path / "state",
        codex_sessions_dir=tmp_path / "codex_없음",
    )
    assert scan_blocked(cfg, now=NOW) == []


def test_마지막_발화를_뽑아_컨텍스트로_쓴다(tmp_path, cfg):
    _write_session(
        tmp_path,
        lines=[
            _line(message={"role": "user", "content": "테스트 고쳐줘"}),
            _line(
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "고치는 중입니다"}],
                }
            ),
            _limit_msg(),
        ],
    )
    s = scan_blocked(cfg, now=NOW)[0]
    assert s.last_user == "테스트 고쳐줘"
    assert s.last_assistant == "고치는 중입니다"


def test_도구_결과는_사용자_발화로_보지_않는다(tmp_path, cfg):
    _write_session(
        tmp_path,
        lines=[
            _line(message={"role": "user", "content": "테스트 고쳐줘"}),
            _line(
                message={
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "exit 0"}],
                }
            ),
            _limit_msg(),
        ],
    )
    s = scan_blocked(cfg, now=NOW)[0]
    assert s.last_user == "테스트 고쳐줘"  # tool_result 가 덮어쓰지 않는다


def test_여러_세션은_최근에_막힌_것부터(tmp_path, cfg):
    _write_session(tmp_path, project="a", session_id="old", lines=[_limit_msg()],
                   mtime=NOW - timedelta(hours=3))
    _write_session(tmp_path, project="b", session_id="new", lines=[_limit_msg()],
                   mtime=NOW - timedelta(hours=1))
    assert [s.session_id for s in scan_blocked(cfg, now=NOW)] == ["new", "old"]
