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
| gemini | `agy` (Antigravity) / `GEMINI_API_KEY` | `agy` |
| grok   | `grok` 로그인 / `XAI_API_KEY` | `grok` |
| local  | `ollama serve` | `ollama serve` |

**하드-노**: 구독 OAuth를 라우터로 프록시하지 않는다. connect는 프로바이더 자체
로그인 창구로만 안내한다.

**정직성(v4.6.1)**: `gemini` 대화형 CLI는 2026-06-18 사멸했고, Antigravity는
**IDE**로 배포된다(파이프형 `agy` CLI 아님). 그래서 gemini 힌트는 `agy` 존재
여부에 따라 동적이다 — 있으면 `effi connect gemini`, 없으면 `GEMINI_API_KEY`
(effi 라우팅) + 대화형은 Antigravity IDE 앱으로 안내하고 `agy CLI 미존재`를
명시한다. 존재하지 않는 로그인 경로를 실측인 양 가리키지 않는다.

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
