# 폴백 — 유료 쿼터 소진 시 로컬로

목표: 유료 플랜(클로드/코덱스)이 레이트리밋·쿼터 소진되면 **자동으로 로컬 모델이 이어받기**.
로컬은 일상 작업 ~80%(단일파일·버그·테스트)를 커버 — 최난도는 유료 복구까지 대기.

> ⚠️ 아래 설정은 **2026 중반 기준 대표 스키마**입니다. 도구가 자주 바뀌니 각 공식 문서에서 최신 키를 확인하세요.

---

## A. claude-code-router (Claude Code 사용 시 추천)

클로드코드 요청을 가로채 프로바이더/모델을 고르고, **순서 폴백 체인**으로 소진 시 로컬로 넘깁니다.

설치·기동:
```bash
npm install -g @musistudio/claude-code-router   # 패키지명 최신 확인
ccr start
# 클로드코드가 ccr의 로컬 엔드포인트를 바라보게 설정 (예: ANTHROPIC_BASE_URL)
```

대표 설정 (`~/.claude-code-router/config.json`) — Ollama를 폴백 말단으로:
```jsonc
{
  "Providers": [
    { "name": "anthropic", "api_base_url": "https://api.anthropic.com",
      "api_key": "sk-ant-…", "models": ["claude-opus", "claude-sonnet"] },
    { "name": "ollama", "api_base_url": "http://localhost:11434",
      "api_key": "ollama", "models": ["qwen2.5-coder:14b", "devstral"] }
  ],
  "Router": {
    "default":     "anthropic,claude-sonnet",   // 기본은 싼 클라우드
    "think":       "anthropic,claude-opus",      // 어려운 판단만 최상위
    "longContext": "anthropic,claude-sonnet",
    "fallback":    ["anthropic,claude-sonnet", "ollama,devstral"]  // 소진 시 로컬
  }
}
```
핵심: `fallback` 체인 말단에 `ollama` → 유료 실패(레이트리밋/쿼터) 시 로컬로 자동 전환.

## B. LiteLLM (여러 프로바이더·하네스)

```bash
pip install 'litellm[proxy]'
litellm --config litellm.config.yaml
```
`litellm.config.yaml`:
```yaml
model_list:
  - model_name: primary
    litellm_params: { model: anthropic/claude-sonnet, api_key: os.environ/ANTHROPIC_API_KEY }
  - model_name: local
    litellm_params: { model: ollama/devstral, api_base: http://localhost:11434 }

litellm_settings:
  # 레이트리밋/타임아웃/쿼터 등 모든 잔여 에러에서 다음으로 폴백
  fallbacks: [{ "primary": ["local"] }]
  num_retries: 2
```

## C. Ollama가 되는 이유
Ollama는 **v0.14.0(2026-01)부터 Anthropic Messages API 네이티브 지원** → 라우터가 로컬 모델을 클로드 호환 백엔드로 그대로 씁니다. 별도 어댑터 불필요.

---

## 반드시 실측 (fail-closed 확인)
설정만 하고 믿지 마세요. **일부러 유료 키를 막거나 쿼터를 소진**시켜, 요청이 로컬로 넘어가 **실제 응답이 오는지** 1회 확인합니다. 안 넘어가면 폴백이 없는 것과 같습니다.

## 로컬 폴백의 현실
- 로컬은 **2~10배 느림**, 최난도 멀티파일/크로스레포는 못 함.
- 계획·아키텍처 품질은 클라우드에 못 미침(환각 위험) → 폴백 중엔 **스코프 좁은 작업**에 한정하고, 유료 복구 후 어려운 건 재검토.
