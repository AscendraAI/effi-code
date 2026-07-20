# 로컬 모델 v4 — 태스크 + 리소스 동적 추천

**고정 모델 금지.** `effi pick --task "…"` 가 (1) 업무 역할 (2) 현재 가용 RAM 으로 고른다.

## 사용

```bash
effi pick                         # 일반 최선
effi pick --task "40개 번역"      # bulk → 작고 빠른 쪽 편향
effi pick --task "좁은 버그픽스"  # implement_narrow → 더 강한 쪽(RAM 허용 시)
effi run -t "번역" "…"
EFFI_LOCAL_MODEL=devstral effi local   # 강제 핀(예외)
```

## 사다리 (catalog/models.json, 2026-07)

| 대략 RAM | 후보 | 역할 |
|---|---|---|
| ≥20GB | `qwen3-coder:30b` | 로컬 상한 에이전트 코딩 |
| ≥15GB | `devstral` | SWE-bench 강점 에이전트 |
| ≥10GB | `gemma4:12b`, `deepcoder:14b` | 추론/코딩 중간 |
| ≥6GB | `ornith:9b`, `qwen2.5-coder:7b` | 에이전트·속도 균형 |
| ≥3GB | `qwen2.5-coder:3b` | 바쁜 16GB |
| ≥1.5GB | `1.5b` 계열 | 최후 수단 |

규칙: footprint ≤ min(가용+로드 − 2GB, 총RAM×60%).

## 2주 갱신

```bash
effi catalog research    # Ollama coding 검색 포함 체크리스트
# catalog/models.json local 섹션 편집
effi catalog bump
```

## 한계

로컬은 클라우드 대체 아님. 좁은 작업·쿼터 백스톱·프라이버시. 소형+MCP 폭주 금지 → `effi local`은 MCP 차단.
