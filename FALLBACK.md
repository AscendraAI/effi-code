# 폴백 — 유료 쿼터 소진 시 로컬로

목표: 유료 플랜(클로드/코덱스)이 레이트리밋·쿼터 소진되면 **로컬 모델이 이어받기**.
로컬은 일상 작업 ~80%(단일파일·버그·테스트)를 커버 — 최난도는 유료 복구까지 대기.

> ✅ **핵심 메커니즘은 실측 검증됨** (2026-07, Ollama 0.24 + qwen2.5-coder:14b):
> Ollama가 **Anthropic Messages API를 네이티브 지원**(v0.14+)하므로, 로컬 모델이 클로드 백엔드로
> 그대로 응답합니다. 아래 curl이 실제로 통과했습니다:
> ```bash
> curl -s http://localhost:11434/v1/messages \
>   -H "content-type: application/json" -H "anthropic-version: 2023-06-01" -H "x-api-key: ollama" \
>   -d '{"model":"qwen2.5-coder:14b","max_tokens":30,"messages":[{"role":"user","content":"Reply: FALLBACK OK"}]}'
> # → {"type":"message","content":[{"type":"text","text":"FALLBACK OK"}], ...}
> ```

---

## Path A — 간단 수동 폴백 (추천 · 지금 바로 됨)

라우터 없이. 유료 쿼터가 바닥나면 **Claude Code를 로컬 Ollama로 향하게** 환경변수만 바꿉니다:

```bash
# 쿼터 소진 시 이 셸에서:
export ANTHROPIC_BASE_URL=http://localhost:11434
export ANTHROPIC_API_KEY=ollama
export ANTHROPIC_MODEL=qwen2.5-coder:14b   # 16GB. 24GB면 devstral
claude    # 이제 로컬 모델로 계속 작업 (스코프 좁은 작업 위주)

# 유료 복구되면 환경변수 해제(또는 새 셸)로 원복
```

장점: 설치 0, 지금 검증됨. 단점: **자동 아님**(당신이 스위치). 청중엔 이게 가장 쉬움.

## Path B — 자동 폴백: claude-code-router (2026 최신)

쿼터 소진/에러 시 **자동으로** 로컬로 넘김. ⚠️ **최신 버전은 설정 방식이 바뀜**(웹 검증 2026-07):

- **Node.js 22+ 필요** (현재 맥: node 26 ✓)
- 설치: `npm install -g @musistudio/claude-code-router`
- 기동: `ccr ui` → 관리 UI `http://127.0.0.1:3458`, 게이트웨이 `http://127.0.0.1:3456`
- 설정은 **UI에서** (구버전의 손수 편집 `config.json` 아님 — 지금은 `~/.claude-code-router/config.sqlite` + 브라우저 UI)
- UI에서: **Providers**에 Anthropic(API 키) + Ollama(`http://localhost:11434`) 등록 → **Router**에 "ordered fallback targets"로 말단에 Ollama → 재시도 정책 설정
- Claude Code를 ccr 게이트웨이로 향하게 (UI의 Agent Config에서 적용)

⚠️ **중요 — 구독 vs API 키**: ccr은 **API 키/크리덴셜 풀** 기반입니다. 즉 자동 라우팅을 쓰려면 Anthropic **API 키**(토큰당 과금)를 프라이머리로 둡니다. **Claude Pro/Max 구독은 ccr을 그대로 통과하지 않습니다.** 구독만 쓴다면 **Path A(수동 스위치)가 현실적**입니다.

## Path C — LiteLLM (여러 프로바이더 폭넓게)
```bash
pip install 'litellm[proxy]'
litellm --config litellm.config.yaml   # fallbacks 로 레이트리밋 시 다음 모델
```
API 키 기반. 여러 하네스/프로바이더를 아우를 때.

---

## 반드시 실측 (fail-closed 확인)
설정만 하고 믿지 마세요. **일부러 유료를 막고** 로컬로 넘어가 실제 응답이 오는지 1회 확인.
(Path A의 핵심 엔드포인트는 위에서 이미 통과 — 나머지는 라우터 연결만.)

## 로컬 폴백의 현실
- 로컬은 **2~10배 느림**, 최난도 멀티파일/크로스레포는 못 함.
- 계획·아키텍처 품질은 클라우드에 못 미침(환각 위험) → 폴백 중엔 **스코프 좁은 작업**에 한정, 유료 복구 후 어려운 건 재검토.
