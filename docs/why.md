# 설계 근거 v4 · 2026-07-20 재확인

## 1. 멀티 프로바이더 라우팅

### 왜 태스크마다 모델을 가르나
- 코딩 토큰의 상당수(~85% 커뮤니티/ofox 분해)는 frontier top이 불필요.
- 보수적 하이브리드 라우팅 실측·보고 **~62% 비용 절감** (공격적 시 80%+ 주장).
- RouteLLM 계열: 강모델 14–26%만 사용해도 품질 ~95% 유지(벤치 인용).

### 프로바이더 강점 (공식 문서 2026-07)
| | 강점 | 카탈로그 1순위 예 |
|---|---|---|
| **Claude** Opus 4.8 / Sonnet 5 / Haiku 4.5 | 에이전트 코딩·판단 / 밸런스 / 저가 | orchestrate, implement, bulk-cloud |
| **OpenAI** GPT-5.6 Sol/Terra/Luna | 전문 추론·코딩 티어 분리 | hard implement, mid, bulk API |
| **Gemini** 3.1 Pro / 3.5 Flash / Flash-Lite | 멀티모달·에이전틱·가성비 | design, research, cheap |
| **Grok** 4.5 / 4.3 / Build | 속도·코딩·검색 툴, 가성비 top | research realtime, implement value |
| **Local** Ollama coding | 공짜·프라이버시·기계 작업 | bulk, quota backstop |

출처:
- https://platform.claude.com/docs/en/about-claude/models
- https://developers.openai.com/api/docs/models
- https://ai.google.dev/gemini-api/docs/models
- https://docs.x.ai/docs/models
- https://ollama.com/search?q=coding

### 캐시 vs 라우팅
- 캐시 읽기 ~90% 할인, 80% 히트 시 **총액 ~32%** 절감(출력 비중).
- 메인 스레드 프로바이더 파편화 → 캐시 붕괴 **~10×**.
- **결론:** 메인=Claude 고정, 라우팅은 격리 서브·로컬·세션 경계.

## 2. 오케스트레이션 구조

### Anthropic multi-agent research (2025)
- Opus 리드 + Sonnet 서브: 리서치 eval **+90.2%**.
- 멀티에이전트 **~15× 토큰**. 코딩은 병렬 분해·실시간 조율이 약해 부적합 경향.
- 노력 스케일링, 구체적 위임, 서브 산출물 파일화, 계획 메모리 고정.

https://www.anthropic.com/engineering/multi-agent-research-system

### Cognition 2026 — what works
- 병렬 **작성** 스웜은 여전히 위험(암묵 결정 충돌).
- 작동: **싱글 라이터 + 주변 지능**(클린 컨텍스트 리뷰, 스마트 프렌드, 매니저).
- 클린 리뷰: PR당 ~2버그, ~58% 심각. 생성 컨텍스트 공유 시 열화(context rot).
- 약 주모델→강 상담 에스컬레이션은 **미해결** → 로컬을 대장으로 쓰지 않음.

https://cognition.com/blog/multi-agents-working

### Claude Agent Teams
- 실험·고비용. 리서치/리뷰/독립 모듈/경쟁 가설에 유리. 팀 3–5.
- https://code.claude.com/docs/en/agent-teams

## 3. 멀티 계정 로테이션

- 목적: 사용량 한도 분산으로 **작업 연속성** 유지.
- 방식: 사용자 설정 `switch_threshold_percent` (기본 80). `usage_percent` 미터 기반.
- API 키 계정 또는 `CLAUDE_CONFIG_DIR` 프로필 격리.
- **구독 OAuth를 제3 라우터에 넣는 자동 프록시는 ToS 위험** → 하지 않음. 토글·키·프로필만.

## 4. 로컬 모델 동적 추천

- 고정 1모델 금지. **태스크 역할 + 가용 RAM** (`effi pick --task`).
- 사다리 예: qwen3-coder:30b → devstral → ornith/gemma4/deepcoder → qwen2.5-coder 7b/3b/1.5b.
- 2주 주기: `effi catalog research` → 수동 편집 → `effi catalog bump`.
  (자동 웹 스크랩은 ID/가격 오탐 위험 → **공식 문서 체크리스트 + 사람 확정**이 안전)

## 5. v4 매핑

| 요구 | 구현 |
|---|---|
| 1. 4사 모델 태스크 최적 + 토큰 최소 | `catalog/*` + `effi route` + cascade |
| 2. Claude 계정 임계 전환 | `effi accounts` + `effi` cloud 훅 |
| 3. 로컬 태스크·리소스 추천 + 2주 갱신 | `effi pick` + `effi catalog` |
| 4. 기획~배포 오케스트레이션 | domains + ORCHESTRATION v4 + templates |

## 재확인일
2026-07-20
