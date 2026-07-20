# 라우팅 & 비용 규율 v4.3

**성능은 필요한 곳에, 절감은 필요 없는 곳에서. 태스크마다 최적 모델.**  
**활성 모드(Apex/Cruise/Sip)가 매트릭스 위에 정책을 덮어쓴다.**

실행기: `effi route "…"`. 매트릭스: `catalog/task-routing.json`. 모드: `catalog/modes.json`.

## 모드가 라우팅을 바꾸는 방식

| 모드 | primary 경향 | 로컬 | 캐스케이드 | 계정 |
|---|---|---|---|---|
| 🚀 Apex | Opus급 코딩, 설계·보안 최상 | primary 금지 | top first | 임계 무시 |
| 🛣 Cruise | 도메인 매트릭스 그대로 | bulk 등 허용 | cheap→mid→top | 임계 준수 |
| ☕ Sip | 로컬/Haiku 우선, Sonnet 천장 | S/M·bulk 적극 | local first | 임계 준수 |

## 프로바이더 맵 (2026-07 카탈로그)

| Provider | Top | Mid (기본 코딩) | Cheap / Bulk |
|---|---|---|---|
| **Claude** | Opus 4.8 / Fable 5 | Sonnet 5 | Haiku 4.5 |
| **OpenAI (Codex)** | GPT-5.6 Sol | GPT-5.6 Terra | GPT-5.6 Luna |
| **Gemini** | 3.1 Pro | 3.5 Flash | 3.1 Flash-Lite |
| **Grok** | 4.5 | 4.3 / Build 0.1 | — |
| **Local** | qwen3-coder:30b / devstral | ornith:9b / gemma4:12b | qwen2.5-coder 7b→1.5b |

> 가격·ID는 카탈로그 기준. 2주마다 `effi catalog research` 후 갱신.

## 도메인 → 1순위 (요약)

| Domain | Primary | 토큰 전략 |
|---|---|---|
| orchestrate / plan / architecture / security | Claude Opus | 판단에만 top |
| implement / refactor / test / deploy | Claude Sonnet | 메인 스레드 캐시 |
| design / multimodal mock | Gemini Flash / image | 비전 강점 |
| research (+ realtime) | Gemini Pro or Grok+search | 병렬 읽기 OK |
| review (clean ctx) | Sonnet; 보안은 Opus | 생성 컨텍스트 분리 |
| bulk / translate / docstring | **Local auto** | 공짜; 검증 필수 |
| implement_hard / debug hard | Opus or GPT Sol or Grok 4.5 | 실패 시만 |

## 캐스케이드

```
start_tier (TRIAGE)
  → verify pass → adopt
  → fail ≤2 times same tier
  → escalate one step (local→cheap→mid→top)
  → top fail → stop + report user
```

## 캐시 규율 (가장 비싼 실수 방지)

| 규칙 | 이유 |
|---|---|
| 메인 = Claude 고정 (기본) | 캐시 연속; 미스 시 입력 ~10× |
| 시스템/규칙 프리픽스 불변 | 가변 프롬프트 = 히트 0% |
| 도구 직렬화 순서 고정 | 비결정 직렬화 = 캐시 붕괴 |
| 라우팅은 격리 서브만 | 서브는 어차피 새 컨텍스트 |

80% 캐시 히트여도 총액 절감은 ~32% 수준(출력 비중) — **불필요한 top 호출 제거**가 더 큼(~62% 보수 하이브리드).

## 로컬

```bash
effi pick --task "번역 40개"     # RAM + 태스크 역할
effi run -t "번역" "…"           # 생성 (stdout)
effi edit path.py "타입 힌트"    # 파일편집 → .effi-new + diff
effi edit --apply-only path.py   # 검토 후 반영
```

고정 `EFFI_LOCAL_MODEL`은 오버라이드일 뿐 기본값이 아님.

## 계정 임계값

```bash
effi accounts threshold 75   # 75% 이상이면 다음 계정
effi accounts meter <id> 75
```

## 금지

- 쉬운 일을 Opus/Sol/Fable에 시키기
- 메인 스레드 프로바이더 중도 교체
- 검증 없이 local/cheap 머지
- 카탈로그 14일+ 방치 (`effi catalog status` stale)
