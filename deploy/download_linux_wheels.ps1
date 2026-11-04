# 외부 인터넷이 되는 PC(Windows PowerShell)에서 사내 폐쇄망 리눅스 서버(x86_64)용
# 오프라인 Python wheel 패키지를 ./wheels 폴더에 미리 내려받는 스크립트입니다.
# 사용법 (리눅스 서버의 Python 버전에 맞춰 -PythonVersion 지정, 기본 3.11):
#   powershell -ExecutionPolicy Bypass -File .\deploy\download_linux_wheels.ps1 -PythonVersion 3.11

param(
    [string]$PythonVersion = "3.11",
    [string]$Platform = "manylinux2014_x86_64"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WheelDir = Join-Path $ProjectRoot "wheels"
New-Item -ItemType Directory -Force -Path $WheelDir | Out-Null

Write-Host "Linux ($Platform, Python $PythonVersion) 오프라인 wheel 다운로드 중 -> $WheelDir"
python -m pip download `
    --dest $WheelDir `
    --platform $Platform `
    --python-version $PythonVersion `
    --only-binary=:all: `
    -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host "완료! 이제 프로젝트 전체(또는 build_release_bundle.py 실행본)를 사내 리눅스 서버로 반입해 ./deploy/start_linux.sh 를 실행하세요."
