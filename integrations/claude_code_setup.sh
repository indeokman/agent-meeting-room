#!/usr/bin/env bash
# Claude Code용 미팅룸 MCP 서버 및 대기 스킬(join-room) 원클릭 등록 스크립트
# 사용법: ./claude_code_setup.sh http://10.20.30.50:8080 agent_a

set -euo pipefail

SERVER_URL="${1:-http://localhost:8080}"
AGENT_ID="${2:-agent_a}"

echo "[1/2] Claude Code에 미팅룸 원격 MCP 등록 중 (${AGENT_ID})..."
claude mcp add --transport http "amr-${AGENT_ID}" "${SERVER_URL}/mcp?agent_id=${AGENT_ID}&client=ClaudeCode"

echo "[2/2] ~/.claude/skills/join-room/SKILL.md 설치 중..."
mkdir -p "${HOME}/.claude/skills/join-room"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/SKILL.md" "${HOME}/.claude/skills/join-room/SKILL.md"

echo "완료! 실행 시 툴 대기 타임아웃 여유를 위해 아래와 같이 실행하세요:"
echo "  MCP_TOOL_TIMEOUT=1800000 claude"
echo "이후 프롬프트에 '/join-room' 또는 '미팅룸 대기해'를 입력하면 즉시 참여합니다."
