# 세팅 가이드

목표: **강한 클로드 오케스트라 + 비용 절감 라우팅 + 쿼터 소진 시 로컬 폴백**을 작동시키기.
소요: 처음 ~10~15분. 자동화는 `./setup.sh` 참고 (아래는 무엇을·왜 하는지).

> ⚠️ 이 도구들(Ollama·claude-code-router·LiteLLM)은 **월 단위로 바뀝니다.** 명령·설정 스키마는
> 세팅 직전 각 공식 문서에서 최신을 한 번 확인하세요. 아래는 2026년 중반 기준 구조입니다.

---

## 1. 하네스 (오케스트레이터)

**Claude Code**를 씁니다 (강한 단일 스레드 오케스트라 = 이 설계의 중심).
이미 쓰고 있다면 이 단계 건너뛰기. 서브에이전트·워커별 모델 지정·컨텍스트 압축이 내장돼 있어 이 설계와 맞습니다.

## 2. 로컬 LLM 환경 (폴백 엔진)

### 2-1. Ollama 설치·실행
```bash
brew install ollama        # 또는 https://ollama.com 앱
ollama serve               # 데몬. localhost:11434 에 API가 뜬다
```
Ollama는 **v0.14.0(2026-01)부터 Anthropic Messages API를 네이티브 지원** → 로컬 모델이 클로드코드/라우터의 백엔드로 바로 꽂힙니다.

### 2-2. 맥 RAM에 맞는 코딩 모델 받기
```bash
# 통합 메모리(RAM)에 맞춰 택1  (자세한 근거: LOCAL-MODELS.md)
ollama pull qwen2.5-coder:14b     # 16GB
ollama pull devstral              # 24GB (에이전트 코딩 특화)
```

### 2-3. 작동 확인
```bash
ollama run qwen2.5-coder:14b "print hello in python"
curl http://localhost:11434/api/tags   # 받은 모델 목록
```

## 3. 라우팅 + 폴백 연결 (비용 절감 + 쿼터 백스톱)

두 갈래 중 하나. **간단히 시작하려면 A.**

### A. claude-code-router (추천 · Claude Code 사용 시)
클로드코드 요청을 가로채 **작업 유형별로 모델을 라우팅**하고, **순서 폴백 체인**으로 쿼터 소진 시 로컬로 넘깁니다. (GitHub 30k+★)
```bash
npm install -g @musistudio/claude-code-router   # 패키지명은 최신 확인
# 설정 파일(예: ~/.claude-code-router/config.json)에
#  - 기본: 클로드(강)  - 가벼운 작업: 싼 모델  - 폴백 말단: ollama 로컬
# 예시 스키마는 FALLBACK.md 참고
ccr start        # 로컬 엔드포인트 기동 → 클로드코드가 이걸 바라보게 설정
```

### B. LiteLLM (여러 하네스·프로바이더를 폭넓게)
```bash
pip install 'litellm[proxy]'
litellm --config litellm.config.yaml   # fallbacks: 레이트리밋/쿼터 시 다음 모델로
```
설정 예시는 `FALLBACK.md`.

## 4. 오케스트레이션 규칙 물리기
`ORCHESTRATION.md`를 코딩 세션의 규칙 파일로 넣습니다(예: 프로젝트 루트). 오케스트레이터가 매 작업 시작 시 읽습니다.

---

## 검증 체크
- [ ] `ollama serve` 떠 있고 `curl localhost:11434/api/tags` 응답
- [ ] 라우터가 로컬 엔드포인트를 띄우고 하네스가 그걸 바라봄
- [ ] 유료 모델 정상 호출 → **일부러 쿼터/키를 막아** 로컬로 폴백되는지 1회 실측 (fail-closed 확인)
- [ ] 메인 스레드가 캐시를 유지하는지(프로바이더 파편화로 캐시 깨지지 않게 — `ROUTING.md`)
