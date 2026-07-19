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

# 4) RAM 감지 → 모델 선택
RAM_BYTES=$(sysctl -n hw.memsize)
RAM_GB=$(( RAM_BYTES / 1024 / 1024 / 1024 ))
say "감지된 통합 메모리: ${RAM_GB}GB"
if   (( RAM_GB >= 30 )); then MODEL="devstral";            NOTE="24GB+ : 에이전트 코딩 특화";
elif (( RAM_GB >= 22 )); then MODEL="devstral";            NOTE="24GB : 에이전트 코딩 특화";
else                          MODEL="qwen2.5-coder:7b";    NOTE="16GB : 빠른 폴백(실측 ~26 tok/s, 14B의 2배)";
fi
echo "권장 모델: ${MODEL}  (${NOTE})"
read -r -p "이 모델을 지금 받을까요? (다운로드 수 GB) [y/N] " ans
if [[ "${ans:-N}" =~ ^[Yy]$ ]]; then
  say "모델 다운로드: ollama pull ${MODEL}"
  ollama pull "${MODEL}"
else
  echo "건너뜀. 나중에: ollama pull ${MODEL}"
fi

# 5) 라우터 안내 (설치는 선택 — 스키마가 자주 바뀌므로 자동화하지 않고 안내)
say "다음: 라우팅 + 폴백 연결"
cat <<'EOF'
  로컬 폴백을 실제로 연결하려면 라우터가 필요합니다 (택1):
    • claude-code-router  (Claude Code 사용 시 추천)
        npm install -g @musistudio/claude-code-router   # 패키지명·설정은 최신 확인
        → 설정 예시: FALLBACK.md
    • LiteLLM             (여러 프로바이더)
        pip install 'litellm[proxy]'  → litellm.config.yaml 의 fallbacks

  ※ 두 도구는 월 단위로 바뀌어 자동 설치를 넣지 않았습니다. FALLBACK.md 참고.
EOF

say "폴백 런처 설치 (선택)"
cat <<'EOF'
  구독 사용자는 라우터 대신 effi 토글을 씁니다 (구독 프록시는 Anthropic ToS 위반):
    ln -s "$PWD/bin/effi" /opt/homebrew/bin/effi    # 또는 PATH에 bin/ 추가
    effi            # 평소 = 구독 Claude
    effi local      # 한도 소진 시 → 로컬(무료)
    effi status     # 상태
EOF

say "완료. 남은 것: effi 를 PATH에 넣기 + ORCHESTRATION.md를 세션 규칙으로 물리기."
