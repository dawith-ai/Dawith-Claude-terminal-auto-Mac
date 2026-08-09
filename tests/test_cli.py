"""CLI 헬퍼 테스트 — 잠금, 쿨다운 판정, run 사이클.

가장 위험한 부분은 잠금(스케줄러가 5분마다 겹쳐 호출)과 쿨다운(같은 세션 반복 재개)이다.
subprocess 는 config.dry_run 으로 차단해 claude 를 실제로 부르지 않는다.
"""

from __future__ import annotations

import os

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from afterlimit import cli
from afterlimit.config import Config
from afterlimit.limits import LimitInfo
from afterlimit.sessions import BlockedSession

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 17, 20, 0, tzinfo=SEOUL)


def _session(sid="abc", reset_hour=19):
    reset = NOW.replace(hour=reset_hour, minute=0)
    return BlockedSession(
        session_id=sid,
        jsonl=None,  # 이 테스트에서는 쓰지 않음
        cwd="/work/repo",
        limit=LimitInfo("usage", reset),
        blocked_at=NOW - timedelta(hours=2),
        started_at=NOW - timedelta(hours=3),
    )


# ── 잠금 ────────────────────────────────────────────────────────────────

def test_잠금은_한_번만_잡힌다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    first = cli._acquire_lock(cfg)
    assert first is not None
    assert cli._acquire_lock(cfg) is None  # 두 번째는 실패
    first.unlink()
    assert cli._acquire_lock(cfg) is not None  # 풀린 뒤엔 다시 잡힘


def test_오래된_잠금은_자동_해제된다(tmp_path):
    import os

    cfg = Config(state_dir=tmp_path / "state")
    lock = cli._acquire_lock(cfg)
    assert lock is not None
    # 잠금을 아주 오래된 것으로 만든다 (timeout + 여유 이상)
    old = datetime.now().timestamp() - (cfg.invoke_timeout_sec + 120)
    os.utime(lock, (old, old))
    assert cli._acquire_lock(cfg) is not None  # stale → 재획득


# ── 쿨다운 판정 ─────────────────────────────────────────────────────────

def test_해제_전이면_대기(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=23)  # 아직 안 풀림 (지금 20시)
    assert cli._due(s, {}, cfg, NOW) is not None


def test_해제_후_기록_없으면_재개_가능(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)  # 풀림
    assert cli._due(s, {}, cfg, NOW) is None


def test_쿨다운_안에_재개했으면_건너뛴다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": (NOW - timedelta(hours=2)).isoformat()}}
    assert cli._due(s, state, cfg, NOW) is not None  # 5시간 쿨다운, 2시간 전 → 대기


def test_쿨다운_지났으면_재개_가능(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": (NOW - timedelta(hours=6)).isoformat()}}
    assert cli._due(s, state, cfg, NOW) is None


def test_naive_타임스탬프_상태도_처리한다(tmp_path):
    """예전 상태 파일이 시간대 없는 값을 남겨도 TypeError 로 죽지 않는다."""
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": "2026-07-17T18:00:00"}}  # naive
    result = cli._due(s, state, cfg, NOW)  # 죽지 않으면 됨
    assert result is not None  # 2시간 전으로 해석 → 쿨다운


def test_손상된_타임스탬프는_쿨다운을_강제하지_않는다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": "쓰레기값"}}
    assert cli._due(s, state, cfg, NOW) is None


# ── 상태 저장 ───────────────────────────────────────────────────────────

def test_상태는_원자적으로_저장되고_다시_읽힌다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    cli._save_state(cfg, {"abc": {"resumed_at": NOW.isoformat()}})
    assert cli._load_state(cfg)["abc"]["resumed_at"] == NOW.isoformat()
    # 임시 파일이 남지 않는다
    assert not cfg.state_file.with_suffix(".tmp").exists()


def test_상태_파일이_없으면_빈_dict(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    assert cli._load_state(cfg) == {}


# ── 실패 백오프 ─────────────────────────────────────────────────────────
#
# 회귀 방지. 2026-08-07 새벽, 한도가 안 풀린 세션을 30분마다 무한히 재개했다.
# 하룻밤 재개 139건 중 137건이 그대로 튕겼고 실제로 이어간 건 2건이었다.
# 원인은 `hit_limit_again` 일 때 상태를 아예 안 남긴 것 — 다음 사이클에
# "재개한 적 없음"으로 보여 또 두드렸다.

def test_한도가_그대로면_다음_사이클에_또_재개하지_않는다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session()
    state = {s.session_id: {"fails": 1, "failed_at": NOW.isoformat()}}

    # 30분 뒤 — 예전엔 여기서 또 재개했다
    assert cli._due(s, state, cfg, NOW + timedelta(minutes=30)) is not None
    # 1시간 백오프가 지나면 다시 시도한다
    assert cli._due(s, state, cfg, NOW + timedelta(hours=1, minutes=1)) is None


def test_실패가_쌓이면_대기가_배로_늘어난다():
    assert cli.backoff_for(1) == timedelta(hours=1)
    assert cli.backoff_for(3) == timedelta(hours=4)
    assert cli.backoff_for(5) == timedelta(hours=16)
    assert cli.backoff_for(99) == timedelta(hours=24)  # 무한정 늘지는 않는다


def test_이어가는_데_성공하면_실패_기록이_지워진다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session()
    # 실패 4회가 쌓여 8시간을 기다리는 중이라도, 성공 재개는 그 기록을 지운다
    state = {s.session_id: {"fails": 4, "failed_at": NOW.isoformat()}}
    assert cli._due(s, state, cfg, NOW + timedelta(hours=1)) is not None

    entry = state[s.session_id]
    entry["resumed_at"] = NOW.isoformat()
    entry.pop("fails"), entry.pop("failed_at")
    # 성공 쿨다운(기본 5시간)만 남는다
    assert cli._due(s, state, cfg, NOW + timedelta(hours=1)) is not None
    assert cli._due(s, state, cfg, NOW + timedelta(hours=6)) is None


def test_손상된_시각은_쿨다운을_강제하지_않는다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session()
    state = {s.session_id: {"fails": 2, "failed_at": "망가진값"}}
    assert cli._due(s, state, cfg, NOW) is None


# ── 죽은 잠금 ───────────────────────────────────────────────────────────
#
# 회귀 방지. 2026-08-07, 실행 중인 잡을 껐다 켜자 죽은 프로세스의 잠금이 남아
# 이후 모든 사이클이 "이미 실행 중입니다"로 아무것도 안 했다. 나이 제한(16분)만
# 있어서, 그동안 무인 재개가 통째로 멎었다.

def test_주인이_죽은_잠금은_즉시_치운다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    lock = cfg.lock_dir
    lock.mkdir(parents=True, exist_ok=True)
    dead = lock / "run.lock"
    dead.write_text("999999")  # 존재할 수 없는 PID

    got = cli._acquire_lock(cfg)
    assert got is not None, "죽은 잠금인데도 막혔다"
    assert int(got.read_text()) == os.getpid()  # 내 것으로 바뀌었다


def test_살아있는_주인의_잠금은_존중한다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    first = cli._acquire_lock(cfg)
    assert first is not None
    # 방금 내가 잡았고 나는 살아 있다 — 두 번째는 막혀야 한다
    assert cli._acquire_lock(cfg) is None


def test_PID를_못_읽으면_잠금을_존중한다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    (cfg.lock_dir / "run.lock").write_text("망가진값", encoding="utf-8")
    assert cli._acquire_lock(cfg) is None  # 애매하면 남기는 쪽


# ── 지출 한도 ───────────────────────────────────────────────────────────
#
# 지출 한도엔 해제 시각이 없다. 가짜 시각을 만들어 두면 그 시각 이후 계속
# "풀렸다"고 보고 풀릴 리 없는 한도를 사이클마다 두드린다(2026-08-07 137회).

def _spend_session(sid="spend1"):
    return BlockedSession(
        session_id=sid, jsonl=None, cwd="/work/repo",
        limit=LimitInfo("spend", None, "monthly spend limit"),
        blocked_at=NOW - timedelta(hours=2), started_at=NOW - timedelta(hours=3),
    )


def test_지출_한도도_재개하되_백오프로_간격을_벌린다(tmp_path):
    """자동 재개에서 통째로 빼면 안 된다 — 한도가 풀렸는지 확인할 길이 사라진다."""
    cfg = Config(state_dir=tmp_path / "state")
    s = _spend_session()
    # 처음엔 해본다 (해제 시각이 없어도 막지 않는다)
    assert cli._due(s, {}, cfg, NOW) is None
    # 한 번 튕기면 1시간은 쉰다
    state = {s.session_id: {"fails": 1, "failed_at": NOW.isoformat()}}
    assert cli._due(s, state, cfg, NOW + timedelta(minutes=30)) is not None
    assert cli._due(s, state, cfg, NOW + timedelta(hours=1, minutes=1)) is None


# ── 상태 저장 경쟁 ───────────────────────────────────────────────────────
#
# 회귀 방지. 2026-08-08: 잘못 쌓인 실패 기록 145개를 지웠는데, 그때 진행 중이던
# 8분짜리 재개가 끝나면서 사이클 시작 시점의 옛 사본으로 전부 되살려 놨다.

def test_저장은_남의_변경을_지우지_않는다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    cli._save_state(cfg, {"a": {"fails": 1}})

    # 사이클이 오래 도는 동안 바깥에서 상태가 바뀐 상황
    cli._save_state(cfg, {"a": {"fails": 1}, "b": {"resumed_at": "x"}})

    # 이 사이클은 자기 항목만 갱신해야 한다
    cli._commit(cfg, lambda s: s.setdefault("a", {}).update({"fails": 2}))

    after = cli._load_state(cfg)
    assert after["a"]["fails"] == 2      # 내 변경은 반영되고
    assert "b" in after                  # 남의 변경도 살아남는다


# ── 인증 만료 전역 게이트 ────────────────────────────────────────────────

def test_로그인_풀린_직후엔_사이클_전체를_건너뛴다():
    state = {cli._GLOBAL: {"auth_expired_at": NOW.isoformat()}}
    assert cli._auth_ready(state, NOW + timedelta(minutes=10)) is not None
    assert cli._auth_ready(state, NOW + timedelta(minutes=31)) is None


def test_인증만료_기록이_없으면_평소대로_진행한다():
    assert cli._auth_ready({}, NOW) is None


# ── Windows 프로세스 존재 확인 ─────────────────────────────────────────────
#
# 회귀 방지. `os.kill(pid, 0)` 은 POSIX 관용구다. Windows CI 실측(2026-08-09):
# `OSError: [WinError 87] The parameter is incorrect` 로 죽었다. ctypes 를 가짜로
# 바꿔치기해서 macOS/Linux 에서도 Windows 분기를 검증한다.

def test_windows_에서는_OpenProcess_로_존재를_확인한다(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "win32")

    class _FakeKernel32:
        def __init__(self, handle):
            self._handle = handle
            self.closed = False

        def OpenProcess(self, access, inherit, pid):
            return self._handle

        def CloseHandle(self, handle):
            self.closed = True

    class _FakeCtypes:
        def __init__(self, handle, last_error=0):
            self._k = _FakeKernel32(handle)
            self._last_error = last_error

        def WinDLL(self, name, use_last_error=True):
            return self._k

        def get_last_error(self):
            return self._last_error

    import sys as _sys

    live = _FakeCtypes(handle=1)
    monkeypatch.setitem(_sys.modules, "ctypes", live)
    assert cli._pid_exists(1234) is True
    assert live._k.closed is True  # 핸들을 닫았다

    denied = _FakeCtypes(handle=0, last_error=5)  # ERROR_ACCESS_DENIED
    monkeypatch.setitem(_sys.modules, "ctypes", denied)
    assert cli._pid_exists(1234) is True  # 접근 거부 = 살아있는 남의 프로세스

    gone = _FakeCtypes(handle=0, last_error=87)  # ERROR_INVALID_PARAMETER
    monkeypatch.setitem(_sys.modules, "ctypes", gone)
    assert cli._pid_exists(1234) is False
