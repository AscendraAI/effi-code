# 폴백 — 유료 쿼터 소진 시 로컬로

목표: 평소 **구독 Claude(+Codex+Gemini)**로 최고 성능, 셋 다 한도 소진되면 **로컬 모델이 이어받기**.

## 왜 "라우터"가 아니라 "토글"인가 (중요)

**Anthropic 2026-02 정책**: Free/Pro/Max **구독의 OAuth 토큰을 Claude Code·claude.ai 외의 도구(라우터·프록시 포함)에 쓰면 약관 위반.** → 구독을 ccr/LiteLLM로 자동 프록시하는 건 **불가(위반).**
그래서 구독 사용자의 정답은 **런처 토글** — 프록시 없이, 한도 도달 시 로컬로 스위치. (아래 `effi`)

> ✅ 핵심 메커니즘 실측 검증됨: Ollama가 Anthropic Messages API 네이티브 지원(v0.14+)이라
> 로컬 모델이 클로드 백엔드로 그대로 응답. `effi local` 이 이걸 env로 꽂아준다.

---

## 쓰는 법 — `effi` 런처 (`bin/effi`)

```bash
export PATH="$PWD/bin:$PATH"   # 또는 bin/* 심볼릭 링크

effi            # CLOUD — accounts.json 있으면 사용량 임계 계정 자동 선택
effi local      # 한도 소진 → 로컬(무료) Claude Code
effi status     # 계정·카탈로그·로컬
effi route "…"  # 태스크별 최적 모델 (4사+로컬)
effi accounts threshold 80
effi accounts meter <id> <percent>
```

한도 메시지 → `effi local` → 유료 복구 후 `effi`(cloud).  
`effi local`: Ollama 기동 + keep-alive + MCP 차단 + **태스크/RAM 기반 모델 pick**.

## 로컬 모델 선택 — 속도 vs 품질

`effi-pick`이 가용 메모리로 자동 선택. 고정은 `EFFI_LOCAL_MODEL=…`.

| 상황 | 모델 | 메모 |
|---|---|---|
| 32GB+ 여유 | `qwen3-coder:30b` | MoE·256K, 로컬 상한에 가깝 |
| 24GB+ | `devstral` | 에이전트 코딩, SWE-bench 46.8% |
| 16GB 여유 | `qwen2.5-coder:7b` | 실측 ~26 tok/s |
| 16GB 바쁨 | `qwen2.5-coder:3b` | 실측 ~52 tok/s |

상세 사다리: `LOCAL-MODELS.md`. 더 빠르게: `brew upgrade ollama` (Apple Silicon MLX 백엔드).

## (선택) API 키를 쓰는 경우 — 진짜 자동 폴백
구독이 아니라 **Anthropic API 키**로 Claude Code를 쓴다면, 그땐 프록시가 ToS-클린 →
claude-code-router(`npm i -g @musistudio/claude-code-router` → `ccr ui`) 또는 LiteLLM의
ordered fallback으로 **한도/에러 시 로컬로 진짜 자동 전환** 가능. 단 토큰당 과금.

---

## 반드시 실측 (fail-closed)
`effi local`로 실제 응답이 오는지 1회 확인 (핵심 엔드포인트는 이미 검증됨 — 위 curl).

## 로컬의 현실
2~10배 느림, 최난도 멀티파일/크로스레포는 못 함, 계획 품질 낮음(환각).
→ 폴백 중엔 **스코프 좁은 작업**에 한정, 어려운 건 유료 복구 후 재검토.

**작은 모델은 도구가 많으면 오작동한다.** MCP 서버가 여러 개 붙어 있으면 7B가 수백 개 도구에
파묻혀 "안녕"에도 랜덤 툴콜을 뱉는다(실측 2026-07). 그래서 `effi local`은 `--strict-mcp-config`로
MCP를 전부 끄고 내장 코딩 도구만 남긴다. **로컬은 열린 대화가 아니라 좁은 코딩 작업용.**
더 줄이려면 `claude --bare`(훅·플러그인·MCP 전부 스킵)도 가능.
