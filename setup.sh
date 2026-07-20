#!/usr/bin/env bash
# effi-code 세팅 스크립트 (macOS / Apple Silicon)
# 하는 일: Ollama 설치·기동 → RAM에 맞는 코딩 모델 다운 → 라우터 안내.
# 안전: 이미 있으면 건너뜀. 파괴적 작업 없음. 설치 전 무엇을 할지 출력.
set -euo pipefail

say() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

say "effi-code 세팅 시작 (macOS)"

# 0) 플랫폼 확인
if [[ "$(uname)" != "Darwin" ]]; then
  echo "이 스크립트는 macOS용입니다. 다른 OS는 SETUP.md를 수동으로 따르세요."; exit 1
fi

# 1) Homebrew
if ! have brew; then
  echo "Homebrew가 없습니다. https://brew.sh 에서 먼저 설치하세요."; exit 1
fi

# 2) Ollama
if have ollama; then
  echo "Ollama 이미 설치됨: $(ollama --version 2>/dev/null | head -1)"
else
  say "Ollama 설치 (brew install ollama)"
  brew install ollama
fi

# 3) Ollama 데몬 기동 (이미 떠 있으면 건너뜀)
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama 데몬 이미 실행 중 (localhost:11434)"
else
  say "Ollama 데몬 백그라운드 기동"
  (ollama serve >/tmp/ollama-serve.log 2>&1 &) || true
  for i in $(seq 1 20); do
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

# 4) RAM 감지 → 모델 선택 (2026-07 사다리: qwen3-coder > devstral > qwen2.5)
RAM_BYTES=$(sysctl -n hw.memsize)
RAM_GB=$(( RAM_BYTES / 1024 / 1024 / 1024 ))
say "감지된 통합 메모리: ${RAM_GB}GB"
if   (( RAM_GB >= 36 )); then MODEL="qwen3-coder:30b";    NOTE="32GB+ : Qwen3-Coder MoE 30B, 256K";
elif (( RAM_GB >= 22 )); then MODEL="devstral";           NOTE="24GB : Devstral agentic coding";
else                          MODEL="qwen2.5-coder:7b";   NOTE="16GB : 빠른 폴백(실측 ~26 tok/s)";
fi
echo "권장 모델: ${MODEL}  (${NOTE})"
echo "(런타임에는 effi-pick이 가용 메모리로 더 작은 모델을 고를 수 있습니다)"
read -r -p "이 모델을 지금 받을까요? (다운로드 수 GB) [y/N] " ans
if [[ "${ans:-N}" =~ ^[Yy]$ ]]; then
  say "모델 다운로드: ollama pull ${MODEL}"
  ollama pull "${MODEL}"
else
  echo "건너뜀. 나중에: ollama pull ${MODEL}"
fi

# 5) bin 실행 권한
chmod +x bin/effi bin/effi-run bin/effi-pick bin/effi-new bin/effi-classify bin/effi-review \
  bin/effi-route bin/effi-accounts bin/effi-catalog bin/effi-init bin/effi-doctor \
  bin/effi-use bin/effi-log 2>/dev/null || true

# 6) 사용자 config 안내
say "다음: PATH + v4 설정"
cat <<'EOF'
  export PATH="$PWD/bin:$PATH"

  # 멀티 Claude 계정 (선택)
  effi accounts init
  effi accounts threshold 80
  # accounts.json 의 api_key_env 에 맞는 환경변수 설정
  # effi accounts meter <id> <0-100>

  # 태스크 라우팅 스모크
  effi route "implement auth middleware + tests"
  effi catalog status

  # 세션 규칙
  ln -s "$PWD/CLAUDE.md" /path/to/project/CLAUDE.md

  구독 OAuth를 제3 라우터에 넣지 마세요 (ToS).
  API 키 자동 페일오버가 필요하면 LiteLLM / claude-code-router (키 전용).
EOF

say "완료. effi help · docs/why.md (v4 근거)"
