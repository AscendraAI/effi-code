# 업무 도메인 오케스트레이션 v4

 effi-code는 코딩만이 아니라 **기획→설계→구현→검증→배포** 전 구간을 같은 루프와 라우팅으로 돌린다.

## 파이프라인

```
plan ──► architecture ──► design ──► implement ──► test ──► review ──► deploy
  │            │             │            │           │         │          │
  Opus       Opus         Gemini       Sonnet      Sonnet   clean-ctx   Sonnet
  PRD        ADR           mock         code        tests    (±Opus)    +approve
```

각 단계는 `tasks/<feature>/` 아래 산출물로 고정한다. 세션이 죽어도 폴더로 재개.

## 단계별 최소 산출물

| 단계 | 파일 | 완료 기준 |
|---|---|---|
| plan | `task.md` 목표·비범위·완료 기준 | 사용자 확인(L+) |
| architecture | `workers/arch/result.md` ADR 요약 | 트레이드오프 명시 |
| design | `workers/design/*` 스펙/목업 경로 | 시각 확인 |
| implement | 실제 소스 | 테스트 존재 |
| test | 테스트 코드 + 실행 로그 | green |
| review | `workers/review/result.md` | 이슈 재현 판정 |
| deploy | `workers/deploy/checklist.md` | dry-run + 승인 |

## 토큰 효율 패턴

1. **계획·설계 산출물을 파일로 고정** → 이후 턴에 전문 재생성 금지, 경로만 참조.
2. **메인 = Sonnet**, 막힐 때만 Opus 승격 (캐스케이드 ≤2).
3. **리서치/디자인 = 격리 서브** (Gemini/Grok) — 요약만 부모로.
4. **대량 문서/문자열 = local** (`effi-run`).
5. **리뷰 = 새 컨텍스트** — 생성 토큰을 리뷰에 재투입하지 않음 (Cognition).

## 도구 차용 (최신 오케스트레이션에서)

| 패턴 | 출처 | effi 적용 |
|---|---|---|
| Lead + readonly subagents | Anthropic Research | research/debug 가설 |
| Single-writer + clean review | Cognition 2026 | VERIFY 게이트 |
| Map-reduce manager | Cognition | XL만 Teams/멀티 |
| Hybrid model routing | ofox / RouteLLM 계열 | `effi route` |
| Prompt-cache continuity | Anthropic caching | main thread lock |
| Effort scaling | Anthropic | S/M/L/XL TRIAGE |
| Artifact filesystem bus | Anthropic appendix | `workers/*/result.md` |

## 배포 게이트

```
deploy domain → dry-run → checklist → (prod면) 사용자 승인 → apply → log COMPLETE
```

강제 푸시·프로덕션 시크릿·과금 리소스 생성은 항상 **승인 게이트**.
