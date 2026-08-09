#!/usr/bin/env bash
# AfterLimit 설치 — macOS(launchd) 와 Linux(systemd) 를 모두 지원한다.
#
#   ./install.sh             설치 (5분마다 실행)
#   ./install.sh --uninstall 제거
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/afterlimit"
BIN_DIR="$HOME/.local/bin"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/io.afterlimit.run.plist"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

die()  { echo "오류: $*" >&2; exit 1; }
info() { echo "  $*"; }

detect_os() {
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux)  echo linux ;;
    *) die "지원하지 않는 OS: $(uname -s) (macOS 와 Linux 만 지원합니다)" ;;
  esac
}

uninstall() {
  case "$(detect_os)" in
    macos)
      launchctl bootout "gui/$(id -u)/io.afterlimit.run" 2>/dev/null || true
      rm -f "$LAUNCH_AGENT"
      info "launchd 에이전트를 제거했습니다."
      ;;
    linux)
      systemctl --user disable --now afterlimit.timer 2>/dev/null || true
      rm -f "$SYSTEMD_DIR/afterlimit.service" "$SYSTEMD_DIR/afterlimit.timer"
      systemctl --user daemon-reload 2>/dev/null || true
      info "systemd 타이머를 제거했습니다."
      ;;
  esac
  rm -f "$BIN_DIR/afterlimit"
  for cmd in "$REPO_DIR"/commands/*.md; do
    [[ -e "$cmd" ]] && rm -f "$HOME/.claude/commands/$(basename "$cmd")"
  done
  echo
  echo "제거했습니다. 상태 파일은 남아 있습니다: $STATE_DIR"
  echo "완전히 지우려면: rm -rf $STATE_DIR"
}

# 방금 설치한 게 실제로 도는지 확인한다. 여기서 조용히 넘어가면 안 된다.
#
# 2026-08-09 실측: install.sh 가 "완료했습니다"를 출력했는데 레포 폴더 밖에서는
# `ModuleNotFoundError: No module named 'afterlimit'` 로 완전히 깨졌다.
# 원인 둘 — ① pipx install 실패(기존 venv 충돌, uv 백엔드에서 흔함)를 감지하지 못하고
# pip 으로 조용히 넘어갔다. ② pip 도 PEP 668(Homebrew Python)로 막히면 쓰던
# "오프라인" 폴백이 설치 시점 python3 의 site-packages 에 .pth 를 심었는데, macOS 는
# python3 가 여러 개 흔해서(system·Homebrew·pyenv) 실행 시점 python3 와 다르면 못 찾는다.
verify_install() {
  local out
  out=$(cd /tmp && env -u PYTHONPATH "$BIN_DIR/afterlimit" --version 2>&1) \
    || die "설치했지만 실행이 안 됩니다: $out"
  [[ "$out" == afterlimit* ]] || die "설치했지만 버전 확인에 실패했습니다: $out"
  info "검증: $out"
}

install_bin() {
  command -v python3 >/dev/null || die "python3 가 필요합니다."
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 이상이 필요합니다."
  command -v claude >/dev/null || info "경고: claude 를 PATH 에서 찾지 못했습니다. 설치는 계속합니다."

  mkdir -p "$BIN_DIR" "$STATE_DIR"

  # 표준 설치를 우선하되, 인터넷/pip 이 없어도 동작하도록 폴백을 둔다.
  if command -v pipx >/dev/null; then
    # --force 는 uv 백엔드에서 "세션 밖에서 만든 venv" 를 만나면 실패한다(재현됨).
    # 실패하면 지우고 새로 깐다 — 업그레이드 시 항상 거치는 정상 경로로 만든다.
    if ! pipx install --force "$REPO_DIR" >/tmp/afterlimit_pipx.log 2>&1; then
      pipx uninstall afterlimit >/dev/null 2>&1 || true
      pipx install "$REPO_DIR" >/tmp/afterlimit_pipx.log 2>&1
    fi
    if [[ -x "$BIN_DIR/afterlimit" ]]; then
      info "pipx 로 설치했습니다."
      verify_install
      return 0
    fi
    info "pipx 설치가 흔적을 못 남겼습니다 — 로그: /tmp/afterlimit_pipx.log"
  fi
  if python3 -m pip --version >/dev/null 2>&1; then
    if python3 -m pip install --user --quiet "$REPO_DIR" 2>/tmp/afterlimit_pip.log; then
      info "pip --user 로 설치했습니다."
      verify_install
      return 0
    fi
    info "pip --user 설치 실패(PEP 668 등) — 로그: /tmp/afterlimit_pip.log"
  fi

  # 최종 폴백: 이 설치 전용 가상환경을 만든다. python3 여러 버전이 섞인 macOS 에서도
  # 실행 파일이 그 venv 의 python 을 절대경로로 직접 부르므로 어긋날 일이 없다.
  info "pipx/pip 을 쓸 수 없어 전용 가상환경으로 설치합니다."
  local venv="$STATE_DIR/venv"
  rm -rf "$venv"
  python3 -m venv "$venv" || die "venv 생성 실패"
  "$venv/bin/pip" install --quiet "$REPO_DIR" || die "venv 안에서 설치 실패"
  cat > "$BIN_DIR/afterlimit" <<EOF
#!/usr/bin/env bash
exec "$venv/bin/python3" -m afterlimit.cli "\$@"
EOF
  chmod +x "$BIN_DIR/afterlimit"
  info "afterlimit 을 전용 가상환경으로 설치했습니다 ($venv)."
  verify_install
}

install_slash_commands() {
  # Claude Code 사용자용 슬래시 명령(/continue, /지속 …). 없으면 조용히 넘어간다.
  local cmd_dir="$HOME/.claude/commands"
  [[ -d "$REPO_DIR/commands" ]] || return 0
  mkdir -p "$cmd_dir"
  local n=0
  for cmd in "$REPO_DIR"/commands/*.md; do
    [[ -e "$cmd" ]] || continue
    cp "$cmd" "$cmd_dir/"
    n=$((n + 1))
  done
  info "슬래시 명령 ${n}개를 $cmd_dir 에 설치했습니다."
}

install_macos() {
  mkdir -p "$(dirname "$LAUNCH_AGENT")"
  sed -e "s|__AFTERLIMIT_BIN__|$BIN_DIR/afterlimit|g" \
      -e "s|__STATE_DIR__|$STATE_DIR|g" \
      -e "s|__PATH__|$BIN_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin|g" \
      "$REPO_DIR/packaging/launchd/io.afterlimit.run.plist" > "$LAUNCH_AGENT"

  launchctl bootout "gui/$(id -u)/io.afterlimit.run" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT"
  info "launchd 에 등록했습니다 (5분 간격)."
}

install_linux() {
  command -v systemctl >/dev/null \
    || die "systemd 가 없습니다. cron 에 다음을 등록하세요: */5 * * * * $BIN_DIR/afterlimit run"
  mkdir -p "$SYSTEMD_DIR"
  cp "$REPO_DIR/packaging/systemd/afterlimit.service" "$SYSTEMD_DIR/"
  cp "$REPO_DIR/packaging/systemd/afterlimit.timer"   "$SYSTEMD_DIR/"
  systemctl --user daemon-reload
  systemctl --user enable --now afterlimit.timer
  info "systemd 타이머를 등록했습니다 (5분 간격)."
  # 로그아웃 후에도 타이머가 돌게 한다. 권한이 없으면 안내만 한다.
  loginctl enable-linger "$USER" 2>/dev/null \
    || info "참고: 'loginctl enable-linger $USER' 를 실행하면 로그아웃 후에도 동작합니다."
}

main() {
  [[ "${1:-}" == "--uninstall" ]] && { uninstall; exit 0; }

  local os; os="$(detect_os)"
  echo "AfterLimit 설치 ($os)"
  install_bin
  install_slash_commands
  case "$os" in
    macos) install_macos ;;
    linux) install_linux ;;
  esac

  echo
  echo "완료했습니다. 확인해 보세요:"
  echo "  afterlimit scan     막힌 세션과 해제 시각"
  echo "  afterlimit config   현재 설정"
  echo
  echo "알림을 받으려면 웹훅 URL 을 넣으세요 (Discord·Slack 등):"
  echo "  export AFTERLIMIT_WEBHOOK_URL='https://...'"
}

main "$@"
