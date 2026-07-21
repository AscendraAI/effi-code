# 로컬 모델 v4 — 태스크 + 리소스 동적 추천

**고정 모델 금지.** `effi pick --task "…"` 가 (1) 업무 역할 (2) 현재 가용 RAM 으로 고른다.

검증일: **2026-07-21** (`catalog/models.json` `last_verified_at`)

## 사용

```bash
effi pick                         # 일반 최선
effi pick --task "40개 번역"      # bulk → 작고 빠른 쪽 편향
effi pick --task "좁은 버그픽스"  # implement_narrow → 더 강한 쪽(RAM 허용 시)
effi run -t "번역" "…"                 # 생성
effi edit path.py "docstring 추가"     # 파일편집 (사이드카)
effi edit --apply-only path.py
EFFI_LOCAL_MODEL=devstral effi local   # 강제 핀(예외)
```

## 사다리 (catalog/models.json, 2026-07-21)

| 대략 RAM | 후보 | 역할 |
|---|---|---|
| ≥19GB | `qwen3-coder:30b` | 로컬 상한 코딩 (MoE, 256K) |
| ≥12–14GB | `devstral` / `devstral:24b`, `qwen3-coder-next` | 에이전트 코딩 · SWE 성향 |
| ≥10–12GB | `qwen2.5-coder:14b`, `gpt-oss:20b`, `gemma4:12b` | 중급 구현 |
| ≥6GB | `ornith:9b`, `qwen2.5-coder:7b` | 속도·에이전트 균형 |
| ≥3GB | `qwen2.5-coder:3b` | 바쁜 16GB (실측 ~52 tok/s) |
| ≥1.5GB | `1.5b` 계열 | 최후 수단 |

규칙: footprint ≤ min(가용+로드 − 2GB, 총RAM×60%).

## 2주 갱신

```bash
effi catalog research    # 공식 문서 + Ollama coding 체크리스트
# catalog/models.json local 섹션 편집 (ram_gb · status · notes)
effi catalog bump
```

## 한계

로컬은 클라우드 대체 아님. 좁은 작업·쿼터 백스톱·프라이버시. 소형+MCP 폭주 금지 → `effi local`은 MCP 차단.  
`effi-edit`는 기본 8k자 초과 파일을 거부한다 (조용한 잘림 방지).
