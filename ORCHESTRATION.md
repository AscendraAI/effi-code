# 운영 규칙 (Orchestration) v4.3

> 세션 규칙으로 물리세요 (`CLAUDE.md`). 매 작업 시작 시 읽습니다.  
> 라우팅 매트릭스: `catalog/task-routing.json` · 모델: `catalog/models.json` · **모드: `catalog/modes.json`**  
> 근거: `docs/why.md` · 계정: `effi accounts` · 비용: `ROUTING.md`

## 3 모드 (프로젝트별 · 작업 중요도 연동)

### 해석 순서
1. `EFFI_MODE` 환경변수  
2. **프로젝트** `.effi/mode` (기본 저장 위치)  
3. 글로벌 `~/.config/effi/state.json`  
4. 없으면 **Cruise**  
   - **프로젝트 핀이 없으면** `effi` / `effi cloud` (TTY)가 모드를 묻는다  
   - 글로벌 모드는 Enter 기본값일 뿐, 질문을 막지 않는다

```bash
effi mode set apex              # 이 프로젝트에 핀 (.effi/mode)
effi mode set sip --global      # 모든 프로젝트 기본
effi mode set cruise --both
effi mode ask                   # 메뉴
effi mode check "보안 감사"     # 중요도 보고 전환 제안
```

| 모드 | 이름 | 한 줄 | 라우팅 성격 |
|---|---|---|---|
| **1** | 🚀 **Apex** | 꼭대기에서 내려찍는다 | 최고 모델, 로컬 primary 금지, 쿼터 임계 무시 |
| **2** | 🛣 **Cruise** | 멀리 가는 순항 | 중상 성능 + 비용 절감 (기본) |
| **3** | ☕ **Sip** | 한 모금씩 | 로컬/cheap 우선, 최소 비용 |

### 작업 중요도에 따른 질문
`effi route` / `use` / `new` 시 작업을 평가한다:

| 중요도 | 예 | 제안 모드 |
|---|---|---|
| **high** | 보안·아키텍처·L/XL·production/긴급 | Apex |
| **medium** | 일반 기능·디버그 | Cruise |
| **low** | 번역·docstring·S·bulk | Sip |

현재 모드가 너무 약하거나(Sip로 보안) 과하면(Apex로 번역) **TTY에서 전환을 묻고**, 동의하면 **프로젝트 `.effi/mode`에 저장**한다.

오케스트레이터는 “풀파워/아껴서” 발화 시 `effi mode set …` 를 제안하고 log에 `[MODE]` 를 남긴다.

## 원칙 (2026 증거)

1. **강한 단일 작성 스레드** — 쓰기는 한 에이전트(Cognition). 병렬 작성 스웜 금지.
2. **서브 = 지능 주입** — 리서치·리뷰·독립 읽기만 병렬. 멀티에이전트 ~15× 토큰(Anthropic).
3. **검증 = 클린 컨텍스트** — 생성 대화 없이 diff만 리뷰(Cognition: PR당 ~2버그, 58% 심각).
4. **비용 = 태스크별 최적 모델** — Claude/Codex/Gemini/Grok/Local 중 **능력×단가** 최선. `effi route`.
5. **메인 스레드 캐시 고정** — 보통 Claude 유지. 프로바이더 파편화 = 캐시 미스 ~10×.
6. **로컬 = 태스크+RAM 동적 추천** — 고정 모델 금지. `effi pick --task "…"`.
7. **계정 로테이션** — Claude 사용량 ≥ 사용자 임계값(기본 80%)이면 다음 계정. `effi accounts`.
8. **파일 = 메모리·버스** — `tasks/<job>/`, 서브 원문은 `workers/`, 부모엔 경로+요약.

---

## 5단계 루프

```
TRIAGE → PLAN → DO → VERIFY → SHIP
              ↑________|  (검증 실패 시 승격, 티어당 ≤2회)
```

### 1) TRIAGE

```bash
effi route "작업 설명"          # domain + model + review
# 또는
effi route --compact "…"
```

| 등급 | 신호 | 기본 |
|---|---|---|
| **S** | 기계적·1파일·명백 | cheap/local |
| **M** | 일반 기능·테스트 | mid (Sonnet/Terra/Flash) |
| **L** | 설계·보안·고난도 디버그 | top (Opus/Sol/Pro) |
| **XL** | 독립 모듈 다수·경쟁 가설 | top 리드 + 읽기 병렬 3–5 |

`log.md`에 한 줄:
```
[TRIAGE] domain=implement grade=M model=claude/claude-sonnet-5 review=clean_context
```

### 2) PLAN — 도메인별 산출물

| 도메인 | 산출 | 선호 모델 (카탈로그) |
|---|---|---|
| plan | PRD/범위/우선순위 → `task.md` | Claude Opus |
| architecture | 트레이드오프·스키마 | Claude Opus / GPT Sol |
| design | 목업·디자인 토큰·컴포넌트 스펙 | Gemini Flash / image |
| research | 조사 메모 + 출처 | Gemini Pro / Grok+search |
| implement* | 코드 | Sonnet / Terra / Grok Build |
| test | 테스트 | Sonnet / Luna(대량) |
| review | 적대 리뷰 리포트 | **다른 컨텍스트** Sonnet/Opus |
| security | 위협 모델·패치 | Opus only |
| deploy | 파이프라인·런북·체크리스트 | Sonnet; prod는 승인 게이트 |
| bulk | 번역·docstring·스캐폴드 | **Local** (`effi-run` 생성 / `effi-edit` 편집) |

L/XL만 긴 계획. 계획은 파일에 고정(컨텍스트 잘림 대비).

### 3) DO — single writer

- **메인이 파일 쓰기.** 서브는 읽기·초안·리뷰.
- 예외: 파일 집합이 **완전 분리**된 독립 모듈 + 사용자 승인 시만 병렬 구현.
- 기계 대량 → 확인 후 로컬 티어:
  - **생성형** (stdout만): `effi run -t "번역" "…"`
  - **파일편집형** (사이드카+diff): `effi edit path "지시"` → 검토 후 `effi edit --apply-only path`
- 위임 brief: `templates/brief.md` (목표·경로·하지 말 것·노력 예산).
- Claude 계정: 세션 시작 시 `effi`가 threshold 기준으로 자동 선택.

#### 로컬 파일편집 (`effi-edit`) — 캐스케이드 최하단

파일편집형 대량작업도 **싼 티어부터**:

| 티어 | 도구 | 언제 |
|---|---|---|
| Local (free) | `effi-edit` | 좁은 스코프·기계적 수정, 쿼터 절약/소진 |
| Mid | Codex / Gemini 하니스 | 로컬이 약하거나 파일이 크기 가드 초과 |
| Top | Claude (메인 스레드) | 다중파일·난도 높은 판단 |

규약:

1. `effi-edit`는 원본을 건드리지 않는다 → `<file>.effi-new` + unified diff.
2. 오케스트레이터/사용자가 diff를 본 뒤 `--apply-only`(또는 `--apply`)로만 반영.
3. 파일이 컨텍스트 안전선(기본 8000자)을 넘으면 **거부** — 잘라서 편집하지 않는다 (잘림 = 데이터 손실).
4. 작은 모델 출력이 비면/깨지면 같은 티어 ≤2회 후 mid로 승격.

### 4) VERIFY

| 등급 | 검증 |
|---|---|
| S | 스모크 1회 |
| M+ | 테스트 + **클린 컨텍스트 적대 리뷰** (`effi review -o …`) |
| L+ | 위 + 통합 diff 리뷰 |

실패 → 같은 티어 ≤2회 → `effi route` 상위 모델로 승격. 무한 루프 금지.

### 5) SHIP

- `task.md` → done · `log.md` `[COMPLETE]`
- 배포 도메인은 **prod 승인 게이트** 필수.

---

## 멀티 프로바이더 사용법 (토큰 절감)

```
메인 세션 (Claude, 캐시 유지)
  ├─ 격리 서브: Codex/Terra 로 구현 초안 (결과 파일만)
  ├─ 격리 서브: Gemini 로 디자인/롱컨텍스트 요약
  ├─ 격리 서브: Grok 로 실시간 리서치
  ├─ 로컬: effi-run (생성) · effi-edit (파일편집·사이드카)
  └─ 리뷰: 새 스레드 + 다른 모델 가능 (생성 컨텍스트 공유 금지)
```

**금지:** 메인 대화를 도중 OpenAI↔Claude로 갈아끼워 캐시 파괴.  
**허용:** 세션 경계·서브·`effi-run` / `effi-edit`에서 프로바이더 변경.

---

## 계정 로테이션

```bash
effi accounts init
effi accounts threshold 80          # 사용자 정의 %
effi accounts meter work-primary 75
effi accounts select                # usage < threshold 인 계정
```

`effi`(cloud) 시작 시 자동 select. 구독 OAuth 프록시 금지 — API 키 또는 프로필 디렉터리 격리.

---

## 카탈로그 갱신 (2주)

```bash
effi catalog research   # 공식 문서 체크리스트
# catalog/models.json, task-routing.json 편집
effi catalog bump       # updated_at + next_review_due(+14d)
```

---

## 승인 게이트

- **진행:** 읽기, `tasks/` 편집, 테스트, 린트, (관례 허용 시) commit.
- **묻기:** 범위 밖 삭제, 강제푸시, **prod 배포**, 시크릿, 비용/범위 2×, Teams 스폰, 로컬 위임, 계정 강제 전환.

## 기록

```
[YYYY-MM-DD HH:MM] [TRIAGE|DECISION|WORKER_CALL|VERIFICATION|ESCALATE|ACCOUNT|FALLBACK|COMPLETE] …
```
