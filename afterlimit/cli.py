"""afterlimit CLI — 스케줄러(launchd/systemd/cron)가 주기적으로 호출한다.

    afterlimit scan    무엇이 막혀 있고 언제 풀리는지 본다 (아무것도 실행하지 않음)
    afterlimit run     풀린 세션을 이어서 실행한다 (스케줄러가 호출하는 것)
    afterlimit config  현재 설정을 보여준다
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from afterlimit import __version__
from afterlimit.config import Config, config_path
from afterlimit.limits import local_tz
from afterlimit.notify import notify
from afterlimit.resume import resume
from afterlimit.sessions import BlockedSession, scan_blocked

__all__ = ["main"]


def _load_state(cfg: Config) -> dict:
    try:
        return json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(cfg: Config, state: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg.state_file)  # 원자적 교체 — 중간에 죽어도 파일이 깨지지 않는다


def _commit(cfg: Config, mutate) -> None:
    """디스크의 최신 상태를 **다시 읽어** 바꾸고 저장한다.

    한 사이클이 재개에 수 분을 쓰는 동안 다른 곳(사람·다른 도구·다음 사이클)이
    상태를 바꿀 수 있다. 사이클 시작 때 읽은 사본을 통째로 덮어쓰면 그 변경이
    조용히 사라진다 — 2026-08-08 실측: 잘못 쌓인 실패 기록 145개를 지웠는데
    진행 중이던 8분짜리 재개가 끝나면서 옛 사본으로 전부 되살려 놨다.
    """
    fresh = _load_state(cfg)
    mutate(fresh)
    _save_state(cfg, fresh)


def _pid_exists(pid: int) -> bool:
    """PID 가 가리키는 프로세스가 지금 살아 있나. POSIX/Windows 둘 다 지원.

    ⚠️ `os.kill(pid, 0)` 은 POSIX 관용구다. Windows 는 신호 0 이라는 게 없어서
    `OSError: [WinError 87] The parameter is incorrect` 로 죽는다 — 실측(CI, 2026-08-09).
    Windows 에선 커널 API(OpenProcess)로 직접 확인한다. 새 의존성은 안 쓴다(zero-dep 유지).
    """
    if sys.platform == "win32":
        import ctypes

        ERROR_ACCESS_DENIED = 5
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        # get_last_error() 가 실제 값을 돌려주려면 이 DLL 핸들을 use_last_error=True 로 열어야 한다
        # (ctypes.windll.kernel32 의 기본 핸들은 그 값을 안 채운다).
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # 접근 거부 = 존재는 하지만 남의 프로세스(POSIX 의 PermissionError 와 대응) → 살아있다고 본다.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        os.kill(pid, 0)  # 신호 0 = 존재 확인만
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 살아 있는 남의 프로세스
    return True


def _owner_alive(lock: Path) -> bool:
    """잠금을 만든 프로세스가 아직 살아 있나.

    확실히 죽었을 때만 False 를 준다. 읽을 수 없거나 남의 프로세스면 살아 있다고 본다 —
    PID 는 재사용되므로, 애매하면 잠금을 남기는 쪽이 안전하다(나이 제한이 결국 치운다).
    """
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    if pid <= 0:
        return True
    return _pid_exists(pid)


def _acquire_lock(cfg: Config) -> Path | None:
    """단일 인스턴스 보장. 스케줄러가 겹쳐 실행해도 두 번 재개하지 않는다."""
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    lock = cfg.lock_dir / "run.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock, flags, 0o600)
    except FileExistsError:
        try:
            age = datetime.now().timestamp() - lock.stat().st_mtime
        except OSError:
            return None
        # 주인이 죽었으면 나이와 상관없이 치운다.
        # 왜: 실행 도중 죽으면(스케줄러 재적재·재부팅·강제종료) 잠금이 남아 16분간
        # 무인 재개가 통째로 멎는다. 2026-08-07 잡을 껐다 켜자 실제로 그렇게 됐다.
        if _owner_alive(lock) and age < cfg.invoke_timeout_sec + 60:
            return None  # 아직 돌고 있다
        lock.unlink(missing_ok=True)  # 죽은 채 남은 잠금 — 치운다
        try:
            fd = os.open(lock, flags, 0o600)
        except FileExistsError:
            return None  # 다른 인스턴스가 방금 선점함
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


#: 재개했는데 한도가 그대로일 때 다음 시도까지 기다리는 시간. 실패가 쌓일수록 배로 늘린다.
#:
#: 왜 필요한가: 실패를 기록하지 않던 시절, 한도가 안 풀린 세션을 매 사이클(30분) 무한히
#: 두드렸다. 하룻밤에 재개 139건 중 137건이 그대로 튕겼다(실제로 이어간 건 2건).
#: 재개가 안 되는 세션일수록 더 자주 시도하는 구조였다.
_BACKOFF = [timedelta(hours=h) for h in (1, 2, 4, 8, 16)]
_BACKOFF_MAX = timedelta(hours=24)

#: 같은 세션이 max-turns 로 이 횟수만큼 연속 미완료되면 자동 재시도만으로는 안 풀린다고
#: 보고 알림을 보낸다 (fails 카운터는 hit_limit_again 과 같은 트랙을 공유한다).
MAX_TURNS_ALERT_FAILS = 3


def backoff_for(fails: int) -> timedelta:
    """실패 `fails` 회 뒤 기다릴 시간. 1·2·4·8·16시간으로 늘고 24시간에서 멈춘다."""
    if fails < 1:
        return timedelta(0)
    return _BACKOFF[fails - 1] if fails <= len(_BACKOFF) else _BACKOFF_MAX


def _at(raw: object, now: datetime) -> datetime | None:
    """상태 파일의 시각 문자열을 읽는다. 손상됐으면 None — 쿨다운을 강제하지 않는다."""
    if not isinstance(raw, str):
        return None
    try:
        t = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=now.tzinfo) if t.tzinfo is None else t  # 예전 naive 기록 보정


#: 세션 id 가 아닌 전역 상태 자리. UUID 와 겹치지 않는다.
_GLOBAL = "_global"


# 전역 지출 한도 대기는 걷어냈다 (2026-08-08).
#
# "지출 한도는 계정 전체 조건이니 한 세션이 튕기면 나머지도 전부 튕긴다" 는 전제로
# 만들었는데, 이어서 잰 값이 그 전제를 뒤집었다. 이어가기에 성공한 세션은 14~278줄,
# 매번 튕긴 세션은 9,499줄이었다 — 계정 조건이 아니라 **세션 크기** 조건이었다.
# 전역으로 막아 두니 한 번의 튕김이 잘 되던 작은 세션까지 몇 시간씩 굶겼다.
# 낭비는 세션별 백오프와 큰 세션 폴백으로 잡는다.


def _due(session: BlockedSession, state: dict, cfg: Config, now: datetime) -> str | None:
    """재개하면 안 되는 이유. None 이면 해도 된다."""
    if not session.limit.is_over(now):
        when = session.limit.reset_at
        if when:
            return f"아직 안 풀림 (해제 {when:%H:%M})"
        # 지출 한도엔 해제 시각이 없다. 시각으로 판단할 수 없으니 아래 **백오프**가 속도를 정한다.
        #
        # ⚠️ 자동 재개에서 통째로 빼면 안 된다(2026-08-07 실수). 한 번 그렇게 했더니 막힌
        #    세션이 전부 지출 한도라 재개가 0건이 됐다 — 헛발질은 막았지만 작업 이어가기도 죽었다.
        #    지출 한도는 간헐적으로 열린다(그날 밤 282초·225초짜리 실제 작업이 실제로 이어졌다).
        #    두드려봐야 열린 걸 안다. 다만 열릴 때까지 매 사이클 두드리면 안 되므로,
        #    실패 1회당 1·2·4·8·16시간, 최대 24시간으로 간격을 벌린다.
        if session.limit.kind not in ("spend", "stalled"):
            return "해제 시각 불명"

    entry = state.get(session.session_id, {})

    if prev := _at(entry.get("resumed_at"), now):
        gap = now - prev
        if gap < timedelta(hours=cfg.resume_cooldown_hours):
            return f"최근에 재개함 ({gap.total_seconds() / 3600:.1f}시간 전)"

    # 재개는 했는데 한도가 그대로였던 경우. 재우지 않으면 같은 세션을 매 사이클 두드린다.
    if failed := _at(entry.get("failed_at"), now):
        fails = int(entry.get("fails", 1) or 1)
        left = backoff_for(fails) - (now - failed)
        if left > timedelta(0):
            return f"한도 그대로였음 {fails}회 · {left.total_seconds() / 3600:.1f}시간 더 기다림"
    return None


def _fmt(session: BlockedSession) -> str:
    reset = session.limit.reset_at
    when = f"{reset:%m-%d %H:%M}" if reset else "불명"
    return f"{session.project}/{session.session_id[:8]}  {session.limit.kind:<11} 해제 {when}"


def cmd_scan(cfg: Config) -> int:
    now = datetime.now(local_tz())
    blocked = scan_blocked(cfg, now)
    if not blocked:
        print("한도로 멈춘 세션이 없습니다.")
        return 0

    state = _load_state(cfg)
    print(f"멈춘 세션 {len(blocked)}개 (현재 {now:%m-%d %H:%M %Z})\n")
    for s in blocked:
        reason = _due(s, state, cfg, now)
        mark = "대기" if reason else "재개 가능"
        print(f"  [{mark}] {_fmt(s)}")
        if reason:
            print(f"           └ {reason}")
    return 0


#: claude CLI 로그인이 풀렸을 때 다시 확인하기까지 기다리는 시간.
#: 사람이 조치해야 풀리는 조건이라 지출 한도와 같은 성격이지만, 이건 세션 크기와
#: 무관하게 **환경 전체**를 막으므로 전역으로 다뤄도 된다(2026-08-08 실측 근거로 확정 —
#: 지출 한도 때와 달리 이번엔 실제로 전 세션이 예외 없이 같은 이유로 막혔다).
AUTH_RETRY_MINUTES = 30


def _auth_ready(state: dict, now: datetime) -> str | None:
    """로그인이 최근에 풀린 걸로 확인됐으면 재개를 건너뛸 이유. 괜찮으면 None."""
    at_raw = state.get(_GLOBAL, {}).get("auth_expired_at")
    if not isinstance(at_raw, str):
        return None
    try:
        at = datetime.fromisoformat(at_raw)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    left = timedelta(minutes=AUTH_RETRY_MINUTES) - (now - at)
    if left <= timedelta(0):
        return None
    return f"claude 로그인이 풀려 있었음 · {left.total_seconds() / 60:.0f}분 뒤 다시 확인 (/login)"


def cmd_run(cfg: Config) -> int:
    lock = _acquire_lock(cfg)
    if lock is None:
        print("이미 실행 중입니다.", file=sys.stderr)
        return 0

    try:
        now = datetime.now(local_tz())
        state = _load_state(cfg)
        resumed = 0

        if reason := _auth_ready(state, now):
            print(f"건너뜀 — {reason}")
            return 0

        for session in scan_blocked(cfg, now):
            if resumed >= cfg.max_resume_per_cycle:
                break
            if reason := _due(session, state, cfg, now):
                print(f"건너뜀 {_fmt(session)} — {reason}")
                continue

            print(f"재개 {_fmt(session)}")
            result = resume(session, cfg)
            resumed += 1

            sid, kind, proj = session.session_id, session.limit.kind, session.project

            if result.auth_expired:
                # 이건 이 세션만의 문제가 아니다. claude CLI 로그인은 전 세션 공통이므로
                # 즉시 멈추고 전역에 남긴다 — 나머지 세션도 같은 이유로 하나씩 튕기며
                # 사이클을 낭비할 이유가 없다. 2026-08-08 실측: 로그인이 9시간 풀려 있는 동안
                # 재개 수십 건이 전부 5~10초 만에 'Not logged in' 으로 튕겼다.
                def _auth_fail(s: dict) -> None:
                    s.setdefault(_GLOBAL, {})["auth_expired_at"] = now.isoformat()

                _commit(cfg, _auth_fail)
                print(f"  └ claude 로그인이 풀려 있습니다. /login 실행 후 다시 시도됩니다"
                      f" ({AUTH_RETRY_MINUTES}분 뒤).")
                notify(cfg, "[afterlimit] claude 로그인이 풀렸습니다 — /login 을 실행해 주세요.")
                break  # 이번 사이클은 여기서 접는다. 남은 세션도 같은 이유로 다 막혀 있다.

            if result.hit_limit_again:
                # 실제로 이어간 게 아니니 성공 쿨다운은 안 준다. 대신 실패를 세어 재운다.
                # 이걸 기록하지 않으면 다음 사이클에 "재개한 적 없음"으로 보여 또 두드린다.
                def _fail(s: dict) -> None:
                    e = s.setdefault(sid, {})
                    e["project"] = proj
                    e["fails"] = int(e.get("fails", 0) or 0) + 1
                    e["failed_at"] = now.isoformat()

                _commit(cfg, _fail)
                state = _load_state(cfg)
                entry = state.get(sid, {})
                wait = backoff_for(entry["fails"]).total_seconds() / 3600
                # ★ 걸린 시간과 출력량을 같이 남긴다. 이게 없으면 "바로 튕김"과
                #   "한참 일하다 끝에 걸림"을 구분할 수 없어, 실제로 진척된 작업까지
                #   실패로 읽게 된다 (2026-08-08 사장님 지적으로 발견).
                tried = " · 새로 시작도 시도함" if result.fallback else ""
                print(f"  └ 한도 재차단({entry['fails']}회) · {result.elapsed_sec:.0f}초 · "
                      f"산출 {result.work_chars}자{tried} → {wait:.0f}시간 뒤 재시도")
                continue

            if not result.ok and result.max_turns_exceeded:
                # `--max-turns` 상한에 걸려 미완료로 끝났다 — 완료가 아니다. resumed_at 을
                # 찍으면 다음 사이클이 5시간 쿨다운에 막혀 사실상 다시 시도되지 않는다
                # (2026-08-10 실측). hit_limit_again 과 같은 fails/backoff 트랙에 남겨
                # 다음 사이클에 다시 붙게 하고, 연속 실패가 쌓이면 사람에게 알린다.
                def _fail_max_turns(s: dict) -> None:
                    e = s.setdefault(sid, {})
                    e["project"] = proj
                    e["fails"] = int(e.get("fails", 0) or 0) + 1
                    e["failed_at"] = now.isoformat()

                _commit(cfg, _fail_max_turns)
                state = _load_state(cfg)
                entry = state.get(sid, {})
                fails = entry["fails"]
                wait = backoff_for(fails).total_seconds() / 3600
                print(f"  └ 최대턴(60) 도달({fails}회) · {result.elapsed_sec:.0f}초 · "
                      f"산출 {result.work_chars}자 → {wait:.0f}시간 뒤 재시도")
                if fails >= MAX_TURNS_ALERT_FAILS:
                    notify(
                        cfg,
                        f"[afterlimit] {session.project} 최대턴(60) 도달이 {fails}회 연속 "
                        f"— 자동 재시도로는 안 풀립니다. 확인이 필요합니다.",
                    )
                continue

            def _ok(s: dict) -> None:
                e = s.setdefault(sid, {})
                e["project"] = proj
                e["resumed_at"] = now.isoformat()
                e.pop("fails", None)  # 이어갔으면 실패 기록을 지운다
                e.pop("failed_at", None)
                s.pop(_GLOBAL, None)  # 한도가 풀렸다는 증거 — 전역 대기도 푼다

            _commit(cfg, _ok)
            state = _load_state(cfg)

            how = "새로 시작" if result.fallback else "이어감"
            # 실패 사유는 stderr 가 비어 있을 때가 많다. 그럴 땐 stdout 앞부분을 보여준다 —
            # 이게 없으면 "실패: " 만 찍혀 원인을 볼 수 없다 (2026-08-08).
            why = result.error.strip() or result.output.strip().replace("\n", " ")
            status = "완료" if result.ok else f"실패: {why[:150]}"
            tail = " · 끝에 한도 재도달" if result.limit_mentioned else ""
            print(f"  └ {how} · {result.elapsed_sec:.0f}초 · {result.work_chars}자 · {status}{tail}")
            notify(cfg, f"[afterlimit] {session.project} {how} — {status}")

        if resumed == 0:
            print("재개할 세션이 없습니다.")
        return 0
    finally:
        lock.unlink(missing_ok=True)


def cmd_config(cfg: Config) -> int:
    print(f"설정 파일: {config_path()}{'' if config_path().exists() else '  (없음 — 기본값 사용)'}")
    print(f"세션 기록: {cfg.projects_dir}")
    print(f"상태 저장: {cfg.state_dir}")
    print(f"claude 실행 파일: {cfg.claude_bin}")
    print(f"시간대: {datetime.now(local_tz()):%Z (%z)}")
    print(f"알림 웹훅: {'설정됨' if cfg.webhook_url else '없음'}")
    print(f"사이클당 최대 재개: {cfg.max_resume_per_cycle}")
    return 0


def _force_utf8_console() -> None:
    """stdout/stderr 를 UTF-8 로 강제한다.

    실측(CI, 2026-08-09): Windows 콘솔은 cp1252 등 로캘 코드페이지를 기본으로 쓴다.
    한글 문자열을 출력하는 순간 `UnicodeEncodeError` 로 죽는다 — 도구 자체가 한글
    메시지를 쓰므로 Windows 에서는 이게 없으면 거의 항상 죽는다. macOS·Linux 는
    보통 이미 UTF-8 이라 조용히 통과한다.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # 리다이렉트 등으로 못 바꾸면 원래 인코딩으로 최선을 다한다


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="afterlimit",
        description="AI 코딩 에이전트가 사용량 한도로 멈춰도, 풀리면 스스로 이어가게 합니다.",
    )
    parser.add_argument("--config", type=Path, help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제로 재개하지 않고 계획만 출력")
    parser.add_argument("--version", action="version", version=f"afterlimit {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("scan", help="막힌 세션과 해제 시각 보기")
    sub.add_parser("run", help="풀린 세션 이어서 실행 (스케줄러가 호출)")
    sub.add_parser("config", help="현재 설정 보기")

    args = parser.parse_args(argv)
    try:
        cfg = Config.load(args.config)
    except ValueError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    from dataclasses import replace

    if args.dry_run:
        cfg = replace(cfg, dry_run=True)

    return {"scan": cmd_scan, "run": cmd_run, "config": cmd_config}.get(
        args.cmd or "scan", cmd_scan
    )(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
