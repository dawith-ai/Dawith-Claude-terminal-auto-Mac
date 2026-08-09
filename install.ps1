# AfterLimit 설치 (Windows) — Task Scheduler 로 5분마다 백그라운드 실행.
# launchd(macOS)/systemd(Linux) 에 대응하는 Windows 경로.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'AfterLimit'

# 이 스크립트 자신의 위치. 어디서 실행하든(더블클릭·다른 디렉토리·절대경로) 흔들리지 않는다.
# ⚠️ 예전엔 `.`(현재 디렉토리)로 pip install 을 했다 — 저장소가 아닌 곳에서 실행하면
# "pyproject.toml 을 못 찾는다"거나 엉뚱한 걸 설치하는 식으로 조용히 어긋났다.
$RepoDir = $PSScriptRoot

# macOS/Linux 의 ~/.local/state/afterlimit 과 같은 규칙(afterlimit/config.py 의 state_dir()).
$StateDir = Join-Path $env:USERPROFILE '.local\state\afterlimit'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "제거했습니다: $TaskName"
    } else {
        Write-Host "등록된 작업이 없습니다."
    }
    return
}

# Python 3.11+ 확인
#
# ⚠️ 예전 형태 `(cmd; $x)` 는 PowerShell 문법 오류다 — 괄호 `(...)` 는 파이프라인 하나만
# 감싼다. 여러 문장을 세미콜론으로 묶으려면 `$(...)` (하위표현식)가 필요하다.
# 2026-08-09 pwsh 파서로 실측: 이 파일은 지금까지 Windows 에서 한 번도 파싱조차
# 안 됐을 가능성이 있다.
$pyOk = $false
try {
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
    $pyOk = ($LASTEXITCODE -eq 0)
} catch {}
if (-not $pyOk) { throw "Python 3.11 이상이 필요합니다." }

function Test-AfterLimitWorks {
    # 방금 만든 실행 경로가 실제로 도는지 확인한다. 여기서 조용히 넘어가면 안 된다.
    #
    # 2026-08-09 macOS 쪽에서 실측: install.sh 가 "완료했습니다"를 출력했는데 저장소
    # 폴더 밖에서는 완전히 깨져 있었다(설치 시점 python 과 실행 시점 python 이 달라서).
    # Windows 도 pip 이 실패해도 스크립트가 멈추지 않는 같은 종류의 함정이 있어
    # 검증 없이는 같은 방식으로 조용히 깨질 수 있다.
    param([string]$Program, [string]$VersionArgs)
    Push-Location $env:TEMP
    try {
        $out = & $Program $VersionArgs.Split(' ') 2>&1 | Out-String
        return ($LASTEXITCODE -eq 0 -and $out.Trim().StartsWith('afterlimit'))
    } catch {
        return $false
    } finally {
        Pop-Location
    }
}

function Get-AfterLimitCommand {
    # 콘솔 스크립트가 PATH 에 있으면 그걸, 없으면 python -m 으로 실행
    $exe = Get-Command afterlimit -ErrorAction SilentlyContinue
    if ($exe) { return @{ Program = $exe.Source; RunArgs = 'run'; VersionArgs = '--version' } }
    return @{ Program = 'python'; RunArgs = '-m afterlimit.cli run'; VersionArgs = '-m afterlimit.cli --version' }
}

# 표준 설치를 우선한다 — 실패해도 스크립트가 멈추지 않으므로 $LASTEXITCODE 를 직접 본다.
Write-Host "afterlimit 설치 중..."
python -m pip install --user --upgrade --quiet $RepoDir 2>&1 | Out-Null
$pipOk = ($LASTEXITCODE -eq 0)

$cmd = $null
if ($pipOk) {
    $cmd = Get-AfterLimitCommand
    if (-not (Test-AfterLimitWorks -Program $cmd.Program -VersionArgs $cmd.VersionArgs)) {
        Write-Host "표준 설치는 됐다고 나왔지만 실행이 안 됩니다 — 전용 가상환경으로 다시 시도합니다."
        $pipOk = $false
    }
}

if (-not $pipOk) {
    # 최종 폴백: 이 설치 전용 가상환경을 만든다. PATH 에 다른 python 이 여러 개 있어도
    # 실행 경로가 이 venv 의 python.exe 를 절대경로로 직접 부르므로 어긋날 일이 없다.
    Write-Host "표준 설치를 쓸 수 없어 전용 가상환경으로 설치합니다."
    $venv = Join-Path $StateDir 'venv'
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "가상환경 생성 실패" }

    $venvPython = Join-Path $venv 'Scripts\python.exe'
    & $venvPython -m pip install --quiet $RepoDir
    if ($LASTEXITCODE -ne 0) { throw "가상환경 안에서 설치 실패" }

    $cmd = @{ Program = $venvPython; RunArgs = '-m afterlimit.cli run'; VersionArgs = '-m afterlimit.cli --version' }
    if (-not (Test-AfterLimitWorks -Program $cmd.Program -VersionArgs $cmd.VersionArgs)) {
        throw "전용 가상환경으로도 설치했지만 실행이 안 됩니다. 로그를 확인해 주세요."
    }
    Write-Host "  전용 가상환경으로 설치했습니다 ($venv)."
}

$action  = New-ScheduledTaskAction -Execute $cmd.Program -Argument $cmd.RunArgs
# 5분 간격으로 무기한 반복 (launchd StartInterval 300 / systemd OnUnitActiveSec=5min 대응)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
# 로그인 세션에서, 창 없이. claude 인증(사용자 홈)을 읽어야 하므로 사용자 계정으로.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "검증: OK ($($cmd.Program))"
Write-Host "등록했습니다: '$TaskName' (5분 간격)."
Write-Host ""
Write-Host "확인:"
Write-Host "  afterlimit scan     # 한도로 멈춘 세션"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
