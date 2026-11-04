#!/usr/bin/env bash
# 사내 리눅스 서버 원클릭 구동 스크립트 (온라인 미러 / 오프라인 wheelhouse 자동 감지)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${AMR_PORT:-8080}"
HOST="${AMR_HOST:-0.0.0.0}"

echo "============================================================"
echo " Agent Meeting Room & LLM Memory Benchmark Hub (Linux)"
echo "============================================================"

if [ ! -d ".venv" ]; then
  echo "[1/3] Python 가상환경(.venv) 생성 중..."
  "${PYTHON_BIN}" -m venv .venv
fi

if [ -d "./wheels" ] && [ "$(ls -A ./wheels 2>/dev/null)" ]; then
  echo "[2/3] 오프라인 패키지 폴더(./wheels)에서 의존성 설치 중 (외부망 불필요)..."
  ./.venv/bin/pip install --no-index --find-links=./wheels -r requirements.txt
elif ! ./.venv/bin/python -c "import fastapi, uvicorn, yaml" >/dev/null 2>&1; then
  echo "[2/3] requirements.txt 패키지 설치 중..."
  ./.venv/bin/pip install -r requirements.txt
else
  echo "[2/3] 백엔드 패키지 설치 확인 완료."
fi

echo "[3/3] 단일 포트 통합 서버 시작 -> http://${HOST}:${PORT}"
echo "      - 웹 대시보드 : http://<리눅스서버IP>:${PORT}"
echo "      - MCP 엔드포인트: http://<리눅스서버IP>:${PORT}/mcp"
exec env AMR_HOST="${HOST}" AMR_PORT="${PORT}" ./.venv/bin/python run.py
