"""한도 메시지 파싱 — 순수 함수, 표준 라이브러리만.

Claude Code 세션 기록에 남는 한도 메시지에서 '언제 풀리는지'를 뽑아낸다.
I/O·알림·스케줄러와 분리돼 있어 어디서든 재사용·테스트할 수 있다.

## 시간대에 대하여 (중요)

Claude 가 표시하는 reset 시각("resets 5:50pm")에는 **시간대가 없다.**
사용자의 로컬 시간으로 렌더링된 벽시계 시각일 뿐이다.
따라서 이 문자열을 절대 시각으로 바꾸려면 **한도가 발생한 시점(anchor)의 시간대**를
그대로 써야 한다. 특정 시간대를 상수로 박으면 그 지역 밖에서는 전부 틀린 시각이 나온다.

이 모듈은 시간대를 전역 상수로 두지 않고 `anchor` 에서 가져온다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Literal

__all__ = ["LimitInfo", "LimitKind", "local_tz", "parse_limit", "parse_codex_limit"]

LimitKind = Literal["usage", "server_rate", "spend", "stalled", "codex"]

#: 한도 메시지로 볼 표지. 하나라도 없으면 한도가 아니다.
LIMIT_PATTERNS: tuple[str, ...] = (
    "session limit",
    "usage limit",
    "5-hour limit",
    "weekly limit",
    "spend limit",
    "rate limited",
)

#: 서버 측 rate limit — 내 사용량 한도가 아니라서 reset 시각이 없다.
SERVER_RATE_MARKERS: tuple[str, ...] = (
    "not your usage limit",
    "temporarily limiting",
    "rate limited",
)

#: "resets 11pm" (시간만) / "resets Jun 6 at 11pm" (월·일 명시)
RESET_RE = re.compile(
    r"resets?\s+"
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})\s+at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}

#: 서버 측 rate limit 재시도 간격. reset 시각이 없으니 짧게 잡고 다시 본다.
SERVER_RATE_RETRY = timedelta(minutes=10)
# (지출 한도용 재시도 간격은 없앴다. 아래 parse_limit 의 spend 분기 주석 참고 —
#  시간으로 풀리는 한도가 아니라 사람이 올려야 하는 한도라, 재시도 간격 자체가 틀린 개념이었다.)
#: 시간만 있는 reset 을 anchor 로 못 맞추면(기록이 오래됐거나 시계가 어긋남) 현재 기준으로 다시 잡는다.
_ANCHOR_STALE = timedelta(hours=12)


@dataclass(frozen=True)
class LimitInfo:
    """한도 하나. `reset_at` 이 None 이면 언제 풀리는지 알 수 없다는 뜻이다."""

    kind: LimitKind
    reset_at: datetime | None
    #: 매칭된 원문 조각. 로그·디버깅용
    raw: str = ""

    def is_over(self, now: datetime) -> bool:
        """지금 재개해도 되는가. reset 시각을 모르면 재개하지 않는다."""
        return self.reset_at is not None and now >= self.reset_at


def local_tz() -> tzinfo:
    """이 기기의 로컬 시간대. 한국이면 KST, 미국이면 그쪽 시간대가 나온다."""
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None  # astimezone() 은 항상 tzinfo 를 채운다
    return tz


def _resolve_dated(month: str, day: str, hour: int, minute: int, anchor: datetime) -> datetime:
    """'Jun 6 at 11pm' 처럼 월·일이 명시된 경우. 연도는 anchor 에서 추론한다."""
    try:
        reset = anchor.replace(
            month=_MONTHS[month.lower()],
            day=int(day),
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
    except ValueError:
        # 2월 30일 같은 실재하지 않는 날짜 — 판단 불가
        return anchor + timedelta(hours=1)

    # 연말→연초를 넘어가는 경우. 한참 과거면 내년으로 본다.
    if reset < anchor - timedelta(days=180):
        reset = reset.replace(year=reset.year + 1)
    return reset


def _resolve_time_only(hour: int, minute: int, anchor: datetime, now: datetime) -> datetime:
    """'11pm' 처럼 시간만 있는 경우. 한도가 걸린 시점(anchor) 이후 가장 가까운 그 시각."""
    reset = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset < anchor:
        reset += timedelta(days=1)  # 자정을 넘김

    # anchor 가 너무 오래됐으면 그 추정을 믿을 수 없다. 현재 기준으로 다시 잡는다.
    if reset <= now - _ANCHOR_STALE:
        reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset < now:
            reset += timedelta(days=1)
    return reset


def parse_limit(text: str, *, anchor: datetime, now: datetime | None = None) -> LimitInfo | None:
    """한도 메시지에서 종류와 해제 시각을 뽑는다. 한도 메시지가 아니면 None.

    Args:
        text: 세션에 기록된 에러 메시지 본문.
        anchor: **한도가 발생한 시점.** tz-aware 여야 한다. reset 시각의 시간대와
            날짜 추론이 전부 여기서 나온다. 보통 세션 파일의 mtime 을 넣는다.
        now: 현재 시각. 생략하면 anchor 의 시간대로 지금을 쓴다. 테스트에서 주입한다.

    Raises:
        ValueError: anchor 가 naive(시간대 없음)인 경우. 시간대를 추측하면
            그 지역 밖에서 조용히 틀린 시각이 나오므로 거부한다.
    """
    if anchor.tzinfo is None:
        raise ValueError("anchor 는 tz-aware 여야 합니다 (시간대를 추측하지 않습니다)")
    if now is None:
        now = datetime.now(anchor.tzinfo)

    lowered = text.lower()
    if not any(p in lowered for p in LIMIT_PATTERNS):
        return None

    # 서버가 건 rate limit — 내 한도가 아니고 reset 시각도 없다. 잠시 뒤 다시 본다.
    if any(m in lowered for m in SERVER_RATE_MARKERS):
        return LimitInfo("server_rate", now + SERVER_RATE_RETRY, "server-side rate limit")

    # 월 지출 한도 — 사람이 콘솔에서 올려야 풀린다. **초기화 시각이라는 게 없다.**
    #
    # 예전엔 `anchor + SPEND_RETRY` 로 가짜 해제 시각을 만들어 뒀다. 그러면 그 시각이 지나는
    # 순간부터 `is_over()` 가 계속 참이 되어, 풀릴 리 없는 한도를 사이클마다 두드린다.
    # 2026-08-07 밤새 재개 139건 중 137건이 이렇게 헛돌았다.
    #
    # reset_at=None 이면 `is_over()` 가 항상 거짓이라 자동 재개 대상에서 빠진다.
    # 시간이 아니라 **사람**이 조건이므로, 그게 정확한 표현이다.
    # 사람이 한도를 올린 뒤엔 세션이 새 메시지를 남기고 그때부터 정상 판정된다.
    if "spend limit" in lowered:
        return LimitInfo("spend", None, "monthly spend limit")

    m = RESET_RE.search(text)
    if not m:
        return LimitInfo("usage", None, "")  # 한도는 맞는데 언제 풀리는지 모름

    month, day, hour_s, minute_s, ampm = m.groups()
    hour = int(hour_s)
    minute = int(minute_s or 0)
    if ampm.lower() == "pm" and hour != 12:
        hour += 12
    elif ampm.lower() == "am" and hour == 12:
        hour = 0

    reset = (
        _resolve_dated(month, day, hour, minute, anchor)
        if month and day
        else _resolve_time_only(hour, minute, anchor, now)
    )
    return LimitInfo("usage", reset, m.group(0))


# ── Codex CLI ──────────────────────────────────────────────────────────────
#
# Codex 는 세션 로그에 구조화된 필드를 남긴다(`codex_error_info: "usage_limit_exceeded"`
# 등). 어떤 오류인지 텍스트로 추측할 필요가 없어 Claude 쪽보다 신뢰도가 높다.
# 다만 "언제 풀리는지"는 여전히 자유 텍스트라 파싱이 필요하다.
#
# 실측 문구 (2026-08 세션 로그 + 라이브 재현):
#   "...try again at 12:09 AM."
#   "...try again at May 3rd, 2026 12:09 AM."
# 뒤쪽은 연도까지 있어 anchor 없이도 절대 시각이 나온다(다만 시간대는 여전히
# 벽시계 표기라 anchor 의 시간대를 그대로 쓴다 — Claude 쪽과 같은 제약).

CODEX_MONTHS = _MONTHS  # 같은 표를 재사용

CODEX_RESET_DATED_RE = re.compile(
    r"try again at "
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s*(am|pm)\.",
    re.IGNORECASE,
)
CODEX_RESET_TIME_ONLY_RE = re.compile(
    r"try again at (\d{1,2}):(\d{2})\s*(am|pm)\.",
    re.IGNORECASE,
)


def parse_codex_limit(text: str, *, anchor: datetime, now: datetime | None = None) -> LimitInfo | None:
    """Codex 세션 로그(`codex_error_info`)나 `codex exec` 출력에서 한도 정보를 뽑는다.

    Args:
        text: 오류 메시지 원문. 예: "You've hit your usage limit. ... try again at 12:09 AM."
        anchor: 오류가 발생한 시점 (tz-aware).
        now: 현재 시각. 생략하면 anchor 의 시간대로 지금을 쓴다.
    """
    if anchor.tzinfo is None:
        raise ValueError("anchor 는 tz-aware 여야 합니다 (시간대를 추측하지 않습니다)")
    if now is None:
        now = datetime.now(anchor.tzinfo)

    if "usage limit" not in text.lower():
        return None

    m = CODEX_RESET_DATED_RE.search(text)
    if m:
        month, day, year, hour_s, minute_s, ampm = m.groups()
        hour = int(hour_s)
        if ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif ampm.lower() == "am" and hour == 12:
            hour = 0
        try:
            reset = anchor.replace(
                year=int(year), month=CODEX_MONTHS[month.lower()], day=int(day),
                hour=hour, minute=int(minute_s), second=0, microsecond=0,
            )
        except ValueError:
            reset = anchor + timedelta(hours=1)  # 2월 30일 같은 실재하지 않는 날짜
        return LimitInfo("codex", reset, m.group(0))

    m = CODEX_RESET_TIME_ONLY_RE.search(text)
    if m:
        hour_s, minute_s, ampm = m.groups()
        hour = int(hour_s)
        if ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif ampm.lower() == "am" and hour == 12:
            hour = 0
        reset = _resolve_time_only(hour, int(minute_s), anchor, now)
        return LimitInfo("codex", reset, m.group(0))

    return LimitInfo("codex", None, text[:200])  # 한도는 맞는데 시각을 못 뽑음
