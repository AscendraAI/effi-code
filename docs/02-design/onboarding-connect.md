# Design — Onboarding & Guided Connect (Layer 0)

> effi-code v4.6 — 세션 시작 온보딩
> Status: **SHIPPED** (구현 완료, 102 테스트)

---

## 1. 목표

사용자가 `claude` 또는 `effi` 로 시작하면:

1. **effi-code가 무엇인지** 한 문단으로 소개한다.
2. **claude · codex · gemini(· grok · local)** 연결 상태를 실측해 보여준다.
3. 미연결 프로바이더를 **직접 붙이도록 가이드/실행**한다.
4. 이번 세션에 무슨 작업을 할지 **인풋을 받아 모드를 설정**한다.

기존 Layer 1(preflight, 연결 상태)·Layer 3(mode suggest)를 재사용하고, 그 앞단에
**소개 + 연결 진행(connect)** 을 추가한 것이 Layer 0이다.

## 2. 정직한 제약 (ToS)

OAuth 로그인은 본질상 대화형이라 완전 자동화가 불가능하다. 그래서 connect는 각
프로바이더의 **1차(first-party) 로그인만** 안내·실행한다:

| 프로바이더 | 연결법 | `effi connect <p>` 실행 |
|---|---|---|
| claude | `claude` 구독 로그인 / `ANTHROPIC_API_KEY` | `claude` |
| codex  | `codex login` (ChatGPT 구독) / `OPENAI_API_KEY` | `codex login` |
| gemini | `GEMINI_API_KEY` (aistudio.google.com/apikey) — 개인용 OAuth 폐기 | (안내만, 실행 없음) |
| grok   | `grok` 로그인 / `XAI_API_KEY` | `grok` |
| local  | `ollama serve` | `ollama serve` |

**하드-노**: 구독 OAuth를 라우터로 프록시하지 않는다. connect는 프로바이더 자체
로그인 창구로만 안내한다.

**Gemini 경로 확정(v4.8.0)**: 두 번의 잘못된 전제를 모두 정리한다.

- v4.6.1의 "gemini CLI 2026-06-18 사멸 → `agy`"는 **틀렸다**. `@google/gemini-cli`는
  계속 배포 중이고(확인 시점 stable v0.52.0), `agy` 바이너리는 존재하지 않는다.
- 그렇다고 `oauth-personal`(*Login with Google*)이 쓸 수 있는 것도 **아니다**.
  Google은 **2026-06-18자로 Gemini Code Assist for individuals** — 무료 티어와
  **유료 Google AI Pro·Ultra 전부** — 에 대한 요청 처리를 중단했다(공식 폐기 공지).
  로그인 자체는 성공하고 `~/.gemini/oauth_creds.json`까지 정상 기록되지만 첫
  호출에서 거부된다:

  ```
  $ gemini -p "say OK"     # rc=55
  IneligibleTierError: This client is no longer supported for Gemini Code Assist
  for individuals … tierId: free-tier, reasonCode: UNSUPPORTED_CLIENT
  ```

  `UNSUPPORTED_CLIENT`는 CLI 번들의 `IneligibleTierReasonCode` enum에 **없다** —
  서버가 내려보내는 코드다. 즉 클라이언트 버전 문제가 아니라 서버 측 차단이며,
  CLI를 올려도 해결되지 않는다.

**유료라고 뚫리지 않는다.** 위 실측은 무료 계정(`tierId: free-tier`)에서 나왔지만,
공식 문서가 세 티어를 함께 명시한다: *"stopped serving requests for the Gemini Code
Assist for individuals, Google AI Pro, and Google AI Ultra tiers"*. AI Pro 구독자가
같은 에러를 보고한 이슈도 열려 있다(google-gemini/gemini-cli#28229, p1).

**단, Standard/Enterprise는 영향 없다.** 같은 문서가 *"access to … Gemini CLI using
Gemini Code Assist Standard or Enterprise subscriptions remain unchanged"*라고 못박는다.
이 경로는 라이선스된 GCP 프로젝트로 동작하므로, effi는 `GOOGLE_CLOUD_PROJECT`가
설정돼 있으면 폐기 판정을 **적용하지 않는다**(레지스트리 `oauth_retired_unless_env`).
멀쩡한 로그인을 죽었다고 말하는 건 거짓 🟢과 똑같은 버그의 거울상이다.

따라서 결론은:

- **개인 계정에서 라우팅 가능한 자격증명은 `GEMINI_API_KEY` 하나**
  (발급: aistudio.google.com/apikey).
- `effi connect gemini`는 **아무것도 실행하지 않고 안내만** 한다. 죽은 로그인을
  띄우면 사용자는 인증에 성공한 뒤 거부당하는 최악의 경로를 밟는다.
- 개인 구독(AI Pro/Ultra) 이전 경로는 **Antigravity CLI `agy`** —
  `curl -fsSL https://antigravity.google/cli/install.sh | bash`. v4.6.1이 가정만
  하고 틀렸던 그 바이너리가 이제 실존하며, 이번엔 **설치해서 재보고 적었다**
  (agy 1.1.7 실측):

  | 항목 | 결과 |
  |---|---|
  | 구독 인증 | ✅ OS 키체인의 IDE 로그인 재사용 — API 키 불필요 |
  | 모델 | Gemini 외 `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b` |
  | `-p/--print` | ✅ 공식 플래그, 답이 stdout으로 나옴 |
  | 종료 | ⚠️ **stdout 종류에 달림.** 파일 리다이렉트(`> out.txt`)는 rc=0으로 ~39초에 정상 종료 / **파이프는 영구 대기**(답은 나옴) |
  | `--print-timeout` | ❌ 종료 제어 못 함. `20s`로 주면 **출력조차 안 나옴** |
  | 지연 | ❌ 사소한 프롬프트도 첫 출력 **38.1초**(2회 동일), 200단어 53.5초 |

  → 레지스트리에는 넣되(구독이 살아있음을 preflight에 보여야 하므로) **라우팅
  기본값은 바꾸지 않는다.** effi는 프로바이더 명령을 실행하지 않고 힌트만 주므로,
  파이프에 물려 끝나지 않는 프로세스를 사용자 모르게 떠넘기는 일은 생기지 않는다.
  지연은 `best_for` 라벨(`구독·기동~40s`)로 표에 드러낸다. 직접 쓸 때의 정답
  형태는 하나뿐이다:

  ```sh
  agy --model gemini-3.1-pro-low -p "…" > out.txt
  ```
- 헤드리스 사용: `gemini -m <model> -p "…"` (`-o json` 가능, 키 필요).

**로그인 판정의 정직성**: 두 단계로 정직해야 한다.

1. 바이너리가 PATH에 있다는 사실은 로그인 증거가 아니다. `oauth_creds`를 선언한
   프로바이더는 **실제 자격증명 파일**(비어있지 않을 것)이 있어야 🟢이고, 없으면
   `미로그인` 🟡다. 자격증명을 파일로 두지 않는 프로바이더(Claude Code → macOS
   Keychain)는 필드를 생략해 CLI-존재 휴리스틱을 유지한다.
2. **자격증명 파일이 있다는 사실도 백엔드가 아직 받아준다는 증거가 아니다.**
   프로바이더가 OAuth 클라이언트를 폐기하면(`oauth_retired`) 디스크의 파일은
   남지만 값어치는 0이다. 이 경우 파일을 무시하고 🟡로 낮추며, 라벨은 `미로그인`이
   아니라 `oauth 폐기 → 키 필요`다 — 사용자는 로그인을 **했기** 때문이다.
   레지스트리에 `oauth_retired`(+`oauth_retired_note`, `api_key_url`)만 넣으면
   코드 변경 없이 같은 처리가 적용된다.

## 3. 아키텍처

```
┌─ Layer 0 · Onboarding & Connect (세션 시작) ────────────────┐
│  intro + 연결 상태표 + 미연결 붙이는 법(+실행) + 모드 설정 유도  │
│  진입점 A: SessionStart 훅 (claude)  — assistant가 대화로 진행 │
│  진입점 B: effi connect (터미널)     — TTY에서 로그인 직접 실행 │
└──────────────┬──────────────────────────────────────────────┘
        재사용  │ Layer 1 preflight(연결 실측) · Layer 3 mode suggest
```

## 4. 코어 API (`effi_core.py`)

- `onboarding_intro() -> str` — 제품 설명 단일 소스(훅·런처·connect 공용).
- `connect_hint(pid, spec) -> dict` — `{login, cli, cmd, api_key_env, note}`,
  레지스트리 기반 + 미지 프로바이더 제네릭 폴백.
- `connect_command(pid, spec) -> dict` — `effi connect <p>`가 exec할 명령.
  `available`은 로그인 바이너리가 PATH에 있을 때만 True(없으면 설치 안내).
- `connect_report(probe) -> dict` — preflight + 프로바이더별 hint + `missing`/`partial`.
- `format_connect(rep, intro, action) -> str` — 사람용 뷰. `action=True`면
  assistant에게 온보딩을 진행하라는 `[effi:action]` 블록 포함.

## 5. 진입점

### A. SessionStart 훅 (`effi-hook-session-start`)
- **모드 미고정(=미온보딩)**: `format_connect(intro=True, action=True)` — 소개 +
  연결법 + assistant 유도. 훅은 대화형 메뉴를 못 띄우므로 assistant가 대화로 진행.
- **모드 고정(=온보딩 완료)**: `format_preflight()` 컴팩트 표만 — 매 세션 재소개 방지.

### B. 런처 / 명령 (`effi connect`, `bin/effi-connect`)
```
effi connect              상태 + 미연결 붙이는 법
effi connect --intro      제품 소개도 함께
effi connect --probe      실 API 도달성 체크
effi connect --json       assistant용 재검사
effi connect <provider>   그 프로바이더 1차 로그인 실행(TTY) / 미설치면 설치 안내
```

## 6. 테스트 (`tests/test_connect.py`, 13)

호스트 CLI/키에 비의존(`_which` 패치 + `os.environ` 제어) — intro 내용, 프로바이더별
hint/cmd, 제네릭 폴백, `connect_command` 가용성, `connect_report` missing/partial,
`format_connect` intro/action 토글 · todo · 전부연결 체크마크.
