# 폴백 — 유료 쿼터 소진 시 로컬로

목표: 평소 **구독 Claude(+Codex+Gemini)**로 최고 성능, 셋 다 한도 소진되면 **로컬 모델이 이어받기**.

## 왜 "라우터"가 아니라 "토글"인가 (중요)

**Anthropic 2026-02 정책**: Free/Pro/Max **구독의 OAuth 토큰을 Claude Code·claude.ai 외의 도구(라우터·프록시 포함)에 쓰면 약관 위반.** → 구독을 ccr/LiteLLM로 자동 프록시하는 건 **불가(위반).**
그래서 구독 사용자의 정답은 **런처 토글** — 프록시 없이, 한도 도달 시 로컬로 스위치. (아래 `sa`)

> ✅ 핵심 메커니즘 실측 검증됨: Ollama가 Anthropic Messages API 네이티브 지원(v0.14+)이라
> 로컬 모델이 클로드 백엔드로 그대로 응답. `sa local` 이 이걸 env로 꽂아준다.

---

## 쓰는 법 — `sa` 런처 (`bin/sa`)

```bash
# PATH에 넣기 (1회)
ln -s "$PWD/bin/sa" /usr/local/bin/sa    # 또는 export PATH="$PWD/bin:$PATH"

sa            # 평소 = CLOUD (구독 Claude)
sa local      # 한도 소진 시 → 로컬(무료)로 Claude Code 실행
sa status     # 현재 로컬 모델/Ollama 상태
```

한도 메시지를 보면 `sa local`로 이어서 작업 → 유료 복구되면 새 창에서 `sa`(cloud).
`sa local`은 Ollama 자동 기동 + **모델 메모리 유지(keep-alive)**로 로드 지연도 없앤다.

## 로컬 모델 선택 — 속도 vs 품질

16GB 맥 실측:

| 모델 | 속도 | 용도 |
|---|---|---|
| **qwen2.5-coder:7b** (기본) | **26 tok/s** | 빠름 — 폴백 기본값 |
| qwen2.5-coder:14b | 13 tok/s | 느리지만 더 정확 |

24GB면 `SA_LOCAL_MODEL=devstral sa local`. 더 빠르게: `brew upgrade ollama`(0.30+는 MLX 백엔드로 Apple Silicon 가속).

## (선택) API 키를 쓰는 경우 — 진짜 자동 폴백
구독이 아니라 **Anthropic API 키**로 Claude Code를 쓴다면, 그땐 프록시가 ToS-클린 →
claude-code-router(`npm i -g @musistudio/claude-code-router` → `ccr ui`) 또는 LiteLLM의
ordered fallback으로 **한도/에러 시 로컬로 진짜 자동 전환** 가능. 단 토큰당 과금.

---

## 반드시 실측 (fail-closed)
`sa local`로 실제 응답이 오는지 1회 확인 (핵심 엔드포인트는 이미 검증됨 — 위 curl).

## 로컬의 현실
2~10배 느림, 최난도 멀티파일/크로스레포는 못 함, 계획 품질 낮음(환각).
→ 폴백 중엔 **스코프 좁은 작업**에 한정, 어려운 건 유료 복구 후 재검토.
