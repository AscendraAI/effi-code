# Design — Model Transparency & Credit Advisor

> effi-code v4.5 방향 설계서
> Branch: `feat/model-transparency-advisor`
> Status: **DRAFT (설계 단계)** — 구현 착수 전 합의용

---

## 1. 목표 (Goal)

사용자가 **이 에이전트가 지금 어떤 AI 모델로 작업 중인지**, 그리고 **어떤 모드로 운용하는 게 지금 작업에 최적인지**를 항상 명확히 알 수 있게 한다.

세부:

1. 세션 시작 시 **codex · gemini · claude · grok** 연결 상태를 확인한다.
2. 각 프로바이더의 사용 여유(크레딧/쿼터)를 **정직하게** 표시한다.
3. 사용자가 **어떤 모드**(Apex/Cruise/Sip)로 운용할지 근거를 갖고 결정하게 돕는다.
4. 작업 도중 주기적으로 사용량을 점검하고, 현재 작업에 **가장 효율적인 모드를 추천**한다.
5. "지금 무슨 모델을 쓰고 있는지"를 **상시 노출**하며 사용자와 의논하며 진행한다.

## 2. Non-Goals & 정직한 제약 (반드시 합의)

**실시간 크레딧 조회는 프로바이더 API로 대부분 불가능하다.** 이 설계는 그 사실 위에 세운다.

| 프로바이더 | 잔여 크레딧 실시간 조회 | 대응 |
|---|---|---|
| Claude 구독(Pro/Max) | ❌ 불가 (rate-limit 방식, 조회 API 없음) | 로컬 추정 원장 |
| Codex / OpenAI | ⚠️ 사실상 폐기·지연 | 로컬 추정 원장 (+ 있으면 조회) |
| Gemini | ⚠️ GCP 빌링, 간단 조회 없음 | 로컬 추정 원장 |
| **Gemini CLI** | 🔴 **사멸됨 (2026-06-18 소비자 요청 중단)** | 대화형은 **Antigravity CLI `agy`**로 전환, API는 별개로 유지 |
| Grok / xAI | ⚠️ 콘솔 위주 | 로컬 추정 원장 |

→ **결정(합의됨): 크레딧 = "로컬 추정 원장(local usage-estimation ledger)".**
연결 상태는 **실측 프로브**로, 크레딧은 **우리가 이번 세션에 쓴 양을 예산 대비 차감한 추정치**로 표기한다. 표기는 항상 `~추정`으로 명시하며, 실시간 크레딧인 척하지 않는다. 수동 입력(`set_meter`)은 오버라이드 폴백으로 유지.

**하지 않는 것**: 프로바이더 대시보드를 스크래핑하거나, 조회 불가한 값을 실측인 양 위조하는 것.

## 3. 아키텍처 — 3계층

```
┌─ Layer 1 · Preflight (세션 시작) ───────────────────────────┐
│  effi preflight → 4-프로바이더 연결 상태표 + 모드 추천        │
│  진입점: Claude Code SessionStart 훅 (신설)                 │
└────────────────────────────────────────────────────────────┘
┌─ Layer 2 · Usage Ledger (사용량 인지) ──────────────────────┐
│  프로바이더별 사용량 원장 → 예산 대비 추정 잔여              │
│  신규: ~/.config/effi/usage-ledger.ndjson                  │
└────────────────────────────────────────────────────────────┘
┌─ Layer 3 · Live Advisor (작업 중) ─────────────────────────┐
│  statusline(활성 모델+모드+추정 여유) + 주기적 모드 넛지     │
│  기존 mode_fit / maybe_adjust_mode_for_task 확장           │
└────────────────────────────────────────────────────────────┘
```

## 4. 데이터 모델

### 4.1 Providers registry (신규 개념)

현재 `accounts.json`은 **Claude 전용**(`select_account`이 `provider=="claude"`만 필터). 이를 프로바이더 일반화한다.

```jsonc
// ~/.config/effi/providers.json  (신규)
{
  "schema_version": 1,
  "providers": {
    "claude": {
      "cli": "claude",
      "credential": { "type": "oauth_or_key", "api_key_env": "ANTHROPIC_API_KEY" },
      "probe": "cli_whoami",          // 연결 확인 방법
      "credit_source": "ledger",       // ledger | api | manual
      "budget": { "unit": "rate_limit_window", "note": "구독 — 조회 불가, 추정만" }
    },
    "codex":  { "cli": "codex",  "credential": {"api_key_env": "OPENAI_API_KEY"}, "probe": "api_models", "credit_source": "ledger",
                "budget": { "unit": "usd", "amount": 0 } },
    // Gemini는 두 경로 분리: (a) 대화형 에이전트 CLI는 gemini→agy(Antigravity) 전환, (b) 라우팅용 API는 키 유지
    "gemini": { "cli": "agy", "cli_legacy": "gemini", "credential": {"api_key_env": "GEMINI_API_KEY", "cli_auth": "google_oauth"},
                "probe": "cli_present_or_api", "credit_source": "ledger",
                "budget": { "unit": "usd", "amount": 0 },
                "note": "gemini CLI 2026-06-18 사멸 → agy(Antigravity). API(GEMINI_API_KEY)는 라우팅용으로 별개 유지" },
    "grok":   { "cli": "grok",   "credential": {"api_key_env": "XAI_API_KEY"},    "probe": "cli_present_or_api", "credit_source": "ledger",
                "budget": { "unit": "usd", "amount": 0 } }
  }
}
```

> 마이그레이션: 기존 `accounts.json`(Claude 계정 회전)은 그대로 유지하고, `providers.json`은 그 위의 상위 레이어. `select_account`은 claude 프로바이더의 세부 계정 선택기로 계속 동작.

### 4.2 Usage ledger (신규)

```jsonc
// ~/.config/effi/usage-ledger.ndjson  (append-only, 한 줄 = 한 이벤트)
{"at":"2026-07-24T14:03:00","provider":"claude","model":"claude-opus-4-8","in":1240,"out":830,"est_usd":0.031,"task":"preflight-design","session":"<id>"}
```

- 이벤트 소스: `launch_plan`/라우팅 결과 + (가능하면) Claude Code 훅의 토큰 사용량.
- 집계: `usage_summary(window)` → 프로바이더별 누적 in/out/usd/요청수.
- 추정 잔여: `budget.amount - Σ est_usd` (usd 예산인 경우), 또는 rate-limit 윈도우 대비 요청 비율.

## 5. Layer 1 — `effi preflight`

세션 시작 시 1회 실행. `doctor()`를 존재확인→**실상태확인**으로 승격하고 4-프로바이더로 일반화한 신규 명령.

### 출력 (예시)

```
effi preflight — 2026-07-24 14:03

  Provider   Connection      Usage (~추정)        Best for
  ────────   ────────────    ─────────────────    ──────────────
  🟢 claude   OAuth ok        ~여유 있음            plan/impl/review
  🟢 codex    key ok, api ok  ~$3.10 / $5.00       bulk/refactor
  🟡 gemini   key ok, no api  ~조회불가             design/research
  🔴 grok     no key          —                    (연결 안 됨)

  추천 모드: 🛣 Cruise
  근거: claude 여유 충분 + codex/gemini 보조 가능 → 균형 운용이 최적.
        고위험(보안/아키텍처) 작업이면 🚀 Apex 권장.

  전환: effi mode set cruise    |    상세: effi providers
```

### 상태 판정

- 🟢 connected: credential 존재 **AND** probe 성공(또는 probe 생략 시 credential 존재)
- 🟡 partial: credential 있으나 probe 실패/불가(예: gemini 조회 불가)
- 🔴 down: credential 없음

### 모드 추천 로직

`recommend_mode(preflight, task_hint?)`:
- claude 🟢 + 여유 충분 → 작업 중요도 높으면 **Apex**, 아니면 **Cruise**
- claude 🟡/🔴 or 여유 낮음 → **Sip** (로컬/저가 우선) 권장
- 기존 `assess_task_importance` 결과와 결합해 밴드별 제안.

## 6. Layer 2 — Usage Ledger & 추정

- `record_usage(provider, model, in, out)` — 라우팅/실행 후 원장에 append.
- `usage_summary(provider=None, since=None)` — 누적 집계.
- `estimate_headroom(provider)` → `{"kind": "usd|rpd|rate_limit|unknown", "used": …, "budget": …, "remaining_pct": …|None}`.
- Claude 구독처럼 `unknown`이면 `remaining_pct=None`, 표기는 "~여유 추정(구독)".
- `set_meter` 수동 오버라이드는 유지하되 "manual override" 라벨.

## 7. Layer 3 — Live Advisor

### 7.1 Statusline
Claude Code 커스텀 statusline 스크립트(`bin/effi-statusline`) 신설:
```
🚀 apex · claude-opus-4-8 · ~$3.10/$5 codex · ⚠ gemini
```
→ 활성 모드 · 활성 코딩 모델 · 프로바이더 추정 여유를 한 줄로 상시 노출.

### 7.2 주기적 넛지
- 트리거: N턴마다 또는 `mode_fit`이 바뀔 때(기존 `maybe_adjust_mode_for_task` 확장).
- 조건: 현재 작업 중요도 밴드 vs 활성 프로바이더 추정 여유가 어긋나면 → "지금 X 모드가 더 적합합니다" 제안 + 근거.
- 남용 방지: 같은 제안 쿨다운(예: 세션당 동일 제안 1회).

### 7.3 훅 통합
- **SessionStart** → `effi preflight`(요약 배너 주입).
- **statusLine** → `effi-statusline`.
- 배선 위치: `config/effi.config.example.json`의 `hooks` 블록 + 설치 시 사용자 `~/.claude/settings.json`에 안내.

## 8. 코드 임팩트 맵

| 파일 | 변경 |
|---|---|
| `lib/effi_core.py` | `doctor()` → 프로브 강화 · `preflight()`/`record_usage()`/`usage_summary()`/`estimate_headroom()`/`recommend_mode()` 신설 · `select_account` 유지 |
| `catalog/providers.json` (신규 예시) + `config/` | providers 레지스트리 예시 |
| `bin/effi-preflight` (신규) | preflight 명령 래퍼 |
| `bin/effi-providers` (신규) | 프로바이더 상세/여유 조회 |
| `bin/effi-statusline` (신규) | statusline 렌더러 |
| `bin/effi` 디스패처 | preflight/providers 서브커맨드 등록 |
| `config/effi.config.example.json` | hooks: SessionStart + statusLine 예시 |
| `tests/` | `test_preflight.py`, `test_ledger.py`, `test_recommend_mode.py` |
| `catalog/modes.json` | (선택) 정책에 프로바이더-중립 폴백 추가 |

> **기술부채 흡수**: 이 작업이 "Claude 전용 accounts → 멀티프로바이더 일반화"라는 기존 부채를 자연스럽게 해소한다.

## 9. 단계별 딜리버리

- **P0 (설계)** — 본 문서 ✅ (commit 71c4e8d)
- **P1 (Layer 1)** ✅ — providers.json + `preflight()`/`probe_provider`/`recommend_mode` + `bin/effi-preflight` + doctor agy 폴백 + 20 테스트 + clean-context 리뷰 반영 (commit bf51ff5). `usage_summary`/`estimate_headroom` 읽기측도 선반영.
- **P2 (Layer 2)** — usage-ledger **쓰기측**(`record_usage`) + `effi providers` 상세 + 라우팅 훅 연동.
- **P3 (Layer 3)** — statusline + 주기 넛지 + SessionStart 훅 배선.

## 10. Resolved Decisions (2026-07-24 확정)

1. **예산 단위 → USD 통일 추정.** 모든 프로바이더를 USD 기준으로 추정한다. 토큰 사용량 × models.json 단가로 `est_usd`를 산출해 예산(USD) 대비 차감. 조회 불가한 구독형(Claude Pro, Antigravity Google 계정)도 "환산 추정 USD"로 표기하되 `~추정(구독)` 라벨 유지.
2. **프로브 강도 → 존재 확인 + 선택적 실호출.** 기본은 credential/CLI 존재 확인(빠름), `--probe` 플래그 또는 preflight 시 선택적으로 실제 API models 호출로 승격.
3. **넛지 빈도 → mode_fit 변화 시 + 최소 5턴 간격.** 확정.
4. **Gemini 경로 → Antigravity CLI(`agy`)로 전환 + API 별개 유지.**
   - **근거**: Gemini CLI는 2026-06-18자로 소비자 요청 처리 중단(사멸). 대체는 Antigravity CLI, 바이너리 `agy`(설치 `~/.local/bin/agy`), 인증은 Google 계정 OAuth.
   - **결정**: 대화형 에이전트 경로는 `gemini`→`agy` 프로브로 교체. 단 effi 라우팅이 Gemini를 **API로 직접 호출**하는 경로(GEMINI_API_KEY, models.json의 gemini api_id)는 사멸과 무관하므로 그대로 유지.
   - **doctor 영향**: `which gemini`(레거시)는 유지하되 없으면 `which agy`로 폴백, `agy`도 없고 API 키만 있으면 🟡(API-only)로 판정.
   - **확인 필요(경미)**: 사용자가 Gemini를 대화형(`agy`)으로 쓸지 API 라우팅으로만 쓸지 — P1에서 providers.json 기본값으로 API-우선 설정 후 필요 시 조정.

## 11. 참고 출처 (Gemini/Antigravity 전환)

- Google Developers Blog — Gemini CLI → Antigravity CLI 전환 공지 (2026-06-18 소비자 중단)
- Antigravity CLI docs/install — 바이너리 `agy`, `curl -fsSL https://antigravity.google/cli/install.sh | bash`, Google 계정 OAuth
