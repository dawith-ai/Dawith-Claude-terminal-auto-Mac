"""재개 실행 테스트 — subprocess 는 목으로 막고, 폴백·한도재발·타임아웃을 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from afterlimit.config import Config
from afterlimit.limits import LimitInfo
from afterlimit.resume import ResumeResult, resume
from afterlimit.sessions import BlockedSession

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 17, 20, 0, tzinfo=SEOUL)


def _session():
    return BlockedSession(
        session_id="abc",
        jsonl=None,
        cwd="/work/repo",
        limit=LimitInfo("usage", NOW.replace(hour=19)),
        blocked_at=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(hours=2),
        last_user="테스트 고쳐줘",
        last_assistant="고치는 중",
    )


def test_dry_run은_아무것도_실행하지_않는다():
    cfg = Config(dry_run=True)
    with patch("afterlimit.resume._run") as run:
        r = resume(_session(), cfg)
        run.assert_not_called()
    assert r.ok and not r.fallback


def test_resume_성공():
    cfg = Config()
    with patch("afterlimit.resume._run", return_value=(0, "완료했습니다", "", 3.0)) as run:
        r = resume(_session(), cfg)
    assert r.ok and not r.fallback
    # --resume 경로를 썼는지
    assert "--resume" in run.call_args[0][0]


def test_구조적_실패시_fresh_폴백():
    cfg = Config()
    # 1차 --resume 는 rc!=0 + 빈 출력 (세션 못 찾음), 2차 fresh 는 성공
    outcomes = [(1, "", "no session", 1.0), (0, "새로 했습니다", "", 2.0)]
    with patch("afterlimit.resume._run", side_effect=outcomes) as run:
        r = resume(_session(), cfg)
    assert r.ok and r.fallback
    assert run.call_count == 2
    # 폴백에는 이전 맥락이 프롬프트에 실린다
    assert "테스트 고쳐줘" in run.call_args[0][0][2]


def test_일하다_한도에_걸리면_폴백하지_않는다():
    """이미 진척이 있었으면 새로 시작하면 안 된다 — 한 일을 버리게 된다."""
    cfg = Config()
    work = "초안을 만들어 커밋했습니다. " * 20  # 200자 넘김
    with patch("afterlimit.resume._run",
               return_value=(0, f"{work}\nusage limit reached", "", 300.0)) as run:
        r = resume(_session(), cfg)
    assert run.call_count == 1          # 폴백 없음
    assert not r.hit_limit_again        # 진척이 있었으니 실패가 아니다


def test_아무것도_못_하고_튕기면_맥락_요약으로_새로_시작한다():
    """세션이 너무 커서 맥락을 싣는 것만으로 한도를 넘는 경우.

    실측(2026-08-08): 이어가기에 성공한 세션은 14~278줄인데, 9,499줄짜리는 매번
    7초 만에 115자(한도 메시지)만 내고 튕겼다. 이럴 땐 마지막 대화만 요약해
    새로 시작하는 쪽이 훨씬 싸고 실제로 이어진다.
    """
    cfg = Config()
    outcomes = [(1, "usage limit reached", "", 7.0), (0, "이어서 마쳤습니다", "", 40.0)]
    with patch("afterlimit.resume._run", side_effect=outcomes) as run:
        r = resume(_session(), cfg)
    assert run.call_count == 2          # 폴백을 시도한다
    assert r.ok and r.fallback
    assert "테스트 고쳐줘" in run.call_args[0][0][2]   # 맥락이 실린다


def test_새로_시작해도_튕기면_백오프가_걸리게_한다():
    cfg = Config()
    lim = (1, "usage limit reached", "", 5.0)
    with patch("afterlimit.resume._run", side_effect=[lim, lim]) as run:
        r = resume(_session(), cfg)
    assert run.call_count == 2
    assert r.hit_limit_again            # 실패로 세어 다음 시도를 재운다


def test_hit_limit_again_판정():
    assert ResumeResult(False, False, "You've hit your usage limit", "", 1.0).hit_limit_again
    assert not ResumeResult(True, False, "다 끝냈습니다", "", 1.0).hit_limit_again


# ── 진척 판정 ───────────────────────────────────────────────────────────
#
# 회귀 방지. 2026-08-08: news-auto 세션이 559초 동안 블로그 초안 5,576자를 만들고
# 커밋(1e22b4c3)까지 했는데 출력 끝에 한도 문구가 있다는 이유로 '실패'로 기록됐다.
# 그렇게 쌓인 가짜 실패로 세션 145개가 최대 6시간 백오프에 갇혀 재개가 멎었다.

_LIMIT = "You've hit your org's monthly spend limit · run /usage-credits to raise it"


def test_한참_일하고_끝에_한도면_실패가_아니다():
    work = "블로그 초안을 완성해 커밋했습니다. " * 20  # 200자 훌쩍 넘김
    r = ResumeResult(True, False, f"{work}\n{_LIMIT}", "", 559.0)
    assert r.limit_mentioned          # 문구는 있다
    assert r.work_chars >= 200        # 그런데 일을 했다
    assert not r.hit_limit_again      # → 실패로 세지 않는다


def test_아무것도_못_하고_한도면_실패다():
    r = ResumeResult(False, False, _LIMIT, "", 3.0)
    assert r.limit_mentioned
    assert r.work_chars < 200
    assert r.hit_limit_again


def test_한도_문구가_없으면_당연히_실패가_아니다():
    r = ResumeResult(True, False, "작업을 이어서 마쳤습니다.", "", 120.0)
    assert not r.limit_mentioned
    assert not r.hit_limit_again


def test_산출량은_한도_안내줄을_빼고_센다():
    r = ResumeResult(True, False, f"{_LIMIT}\n짧은답", "", 5.0)
    assert r.work_chars == len("짧은답")   # 안내 문구는 산출로 치지 않는다


# ── 인증 만료 ────────────────────────────────────────────────────────────
#
# 회귀 방지. 2026-08-08: claude CLI 로그인이 9시간 풀려 있는 동안 afterlimit 이
# 재개를 수십 번 시도했고, 매번 5~10초 만에 'Not logged in · Please run /login'
# 만 받고 실패했다. 세션 크기·한도와 무관한 환경 전체의 전제조건이었다.

def test_로그인_풀림_문구를_인식한다():
    r = ResumeResult(False, False, "Not logged in · Please run /login", "", 5.0)
    assert r.auth_expired


def test_평범한_실패는_인증만료가_아니다():
    r = ResumeResult(False, False, "usage limit reached", "", 5.0)
    assert not r.auth_expired

def test_max_turns_도달을_인식한다():
    r = ResumeResult(False, False, "", "Error: Reached max turns (60)", 30.0)
    assert r.max_turns_exceeded
    assert not r.hit_limit_again        # 한도 문구가 아니라 완전히 다른 실패 종류다
    assert not r.auth_expired

def test_정상_완료는_max_turns_도달이_아니다():
    r = ResumeResult(True, False, "작업을 이어서 마쳤습니다.", "", 120.0)
    assert not r.max_turns_exceeded
