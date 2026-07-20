<div align="center">

# effi-code

### 비용을 의식한 멀티 프로바이더 코딩 오케스트레이션

**CLI:** `effi` · **버전:** [`4.4.1`](VERSION)

**Claude · Codex (OpenAI) · Gemini · Grok · Local** 중 업무에 맞는 모델로 라우팅하고,  
**Apex / Cruise / Sip** 세 모드로 성능·비용을 조절하며, 쿼터가 바닥나도 작업을 이어갑니다.

[English](README.md) · [한국어](README_ko.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml/badge.svg)](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20·%20Linux-lightgrey)
![Harness](https://img.shields.io/badge/harness-Claude%20Code-6c47ff)
![Version](https://img.shields.io/badge/version-4.4.1-green)

[빠른 시작](#빠른-시작) ·
[모드](#모드-apex--cruise--sip) ·
[동작 방식](#동작-방식) ·
[명령어](#명령어) ·
[저장소 구조](#저장소-구조) ·
[문서](#문서) ·
[개발](#개발) ·
[기여](#기여) ·
[라이선스](#라이선스)

</div>

---

## effi-code란?

**프레임워크도, 백그라운드 서버도 아닙니다.**  
파일·CLI·일하는 방식만으로 Claude Code 세션을 **비용 감각 있는 리드 오케스트레이터**로 만듭니다.

| 기둥 | 아이디어 |
|------|----------|
| **Route** | 업무 → *충분히 좋은* 가장 싼 모델 (도메인 매트릭스 + 활성 모드) |
| **Mode** | 🚀 Apex · 🛣 Cruise · ☕ Sip — 프로젝트별, 언제든 전환 |
| **Write once** | 쓰기는 한 명; 헬퍼는 경로 + 짧은 요약만 반환 |
| **Verify clean** | **깨끗한 컨텍스트**에서 적대 리뷰 (생성 대화와 분리) |
| **Survive quota** | 다중 계정 로테이션 + 무료 로컬(Ollama) 백스톱 |

> **성능이 필요한 곳엔 최고 모델. 나머지는 싼 모델. 한도가 바닥나면 로컬.**

설계 근거는 2026년 연구·실무 측정에 기반합니다 — [`docs/why.md`](docs/why.md).

### 인증: 로그인만으로 충분 (API 키는 선택)

**핵심 루프를 돌리는 데 Anthropic/OpenAI/Google API 키는 필요 없습니다.**

| 경로 | 필요한 것 | 설명 |
|------|-----------|------|
| **기본 리드** | Claude Code **구독/플랜 로그인** (`claude` 한 번 브라우저·기기 로그인) | `effi` / `effi cloud`가 그 세션을 씀 — `ANTHROPIC_API_KEY` 불필요 |
| **헬퍼** | 각 도구의 **앱/CLI 로그인** (Codex, Gemini CLI, Grok 등) | 제품이 지원하면 로그인만으로 가능 |
| **로컬** | Ollama + 받은 모델 | 클라우드 과금 없음 · 벤더 API 키 없음 |
| **선택** | `effi accounts`용 API 키 | **다중 키 로테이션**, 미터링, 순수 API 백엔드가 필요할 때만 |

라우팅은 “어떤 모델이 맞는지”를 추천하고, **결제 방식은 보통 플랜 로그인**입니다.  
API 키는 필수 조건이 아니라 **업그레이드 옵션**입니다.

---

## 빠른 시작

### 준비물

- macOS 또는 Linux (로컬 모델은 Apple Silicon 검증이 두터움)
- [Claude Code](https://claude.com/claude-code)가 PATH에 있고, **평소 쓰던 플랜으로 로그인**된 상태
- 선택: [Ollama](https://ollama.com) — 로컬 / 쿼터 폴백 (**클라우드 API 불필요**)
- 선택: Codex / Gemini / Grok CLI — 각 제품 방식으로 로그인 후 격리 서브태스크
- 선택: API 키 — 다중 계정 자동화·순수 API용 ([`docs/accounts.md`](docs/accounts.md))

### 설치

```sh
git clone https://github.com/AscendraAI/effi-code.git
cd effi-code
./setup.sh                          # Ollama + 권장 로컬 모델 (macOS)
export PATH="$PWD/bin:$PATH"        # 또는 bin/* 를 /opt/homebrew/bin 에 링크

# 한 번만: 아직 안 했다면 Claude Code 구독 로그인
claude                              # 브라우저/기기 로그인 후 종료
```

### 아무 앱 저장소에 연결

```sh
cd /path/to/your-app
effi init                 # tasks/ · CLAUDE.md · .effi/
effi mode ask             # Apex / Cruise / Sip 선택 (이 프로젝트에 저장)
effi doctor               # 상태 점검 (API 키 없어도 OK)
effi use "레이트리밋 미들웨어 + 테스트 추가"
effi new auth-rate "레이트리밋 미들웨어 + 테스트 추가"
effi                      # Claude Code 로그인 세션 사용 — API 키 불필요
```

구현 후:

```sh
effi review -o tasks/auth-rate/workers/review
effi log auth-rate COMPLETE "배포 완료"
```

---

## 모드 (Apex · Cruise · Sip)

모드는 기본이 **프로젝트 단위** 핀입니다 (`.effi/mode`).  
해석 순서: `EFFI_MODE` 환경변수 → **프로젝트** → 글로벌 `~/.config/effi` → **Cruise**.

| | 모드 | 적합한 때 | 라우팅 성향 |
|---|------|-----------|-------------|
| 🚀 | **Apex** | 어려운 설계·보안, “비용 신경 안 씀” | 최상위 모델(Opus급); **로컬 primary 금지**; 계정 사용량 임계 무시 |
| 🛣 | **Cruise** | 일상 기능 개발 (기본값) | 도메인 매트릭스; 검증 실패 시에만 승격 |
| ☕ | **Sip** | 단순·대량 작업 또는 쿼터 부족 | 로컬·저가 우선; 어려운 구간은 Sonnet 천장 |

```sh
effi mode set apex                 # 이 프로젝트에 핀
effi mode set sip --global         # 모든 프로젝트 기본
effi mode set cruise --both
effi mode check "보안 감사"        # 중요도 → 전환 제안
```

### 작업 중요도 프롬프트

`effi route` / `use` / `new` 시 작업을 평가합니다.

| 중요도 | 예 | 제안 |
|--------|-----|------|
| **high** | 보안, 아키텍처, L/XL, 프로덕션·긴급 | Apex |
| **medium** | 일반 기능, 디버그 | Cruise |
| **low** | 번역, docstring, bulk, 등급 S | Sip |

활성 모드가 그 구간에 비해 너무 약하거나 과하면, **TTY 세션에서 전환을 물은 뒤** 프로젝트에 핀합니다.

---

## 동작 방식

### 핵심 루프

```
TRIAGE → PLAN → DO → VERIFY → SHIP
```

1. **TRIAGE** — `effi route` / `use`: 도메인, 등급, 모델, 리뷰, 모드  
2. **PLAN** — `tasks/<작업>/task.md`에 짧은 계획 (L/XL은 길게)  
3. **DO** — 메인 클로드 스레드가 **단일 작성자**; 헬퍼는 격리된 일만  
4. **VERIFY** — 테스트 + 클린 컨텍스트 리뷰 (`effi review`)  
5. **SHIP** — `[COMPLETE]` 기록; 프로덕션 배포는 승인 게이트  

### 프로바이더 (카탈로그 기반)

| 프로바이더 | 역할 예 |
|------------|---------|
| **Claude** | 메인 스레드(캐시 연속), Opus 판단, Sonnet 구현 |
| **OpenAI / Codex** | 격리된 구현·리뷰 서브태스크 |
| **Gemini** | 디자인, 멀티모달, 긴 컨텍스트 리서치 |
| **Grok** | 실시간 리서치, 가성비 코딩 |
| **Local (Ollama)** | 기계적 대량 작업 + 쿼터 백스톱 (`effi pick`이 가용 RAM 기준) |

매트릭스 위치:

- [`catalog/task-routing.json`](catalog/task-routing.json) — 도메인 → 기본 모델  
- [`catalog/models.json`](catalog/models.json) — 모델 사다리·비용  
- [`catalog/modes.json`](catalog/modes.json) — Apex / Cruise / Sip 정책  

### 라우팅 예시 (Cruise)

```text
$ effi route --compact "분산 트랜잭션 아키텍처 재설계"
domain=architecture … model=claude/claude-opus-4-8 cost=high

$ effi route --compact "레이트리밋 미들웨어와 단위 테스트 추가"
domain=implement … model=claude/claude-sonnet-5 cost=mid

$ effi route --compact "랜딩 페이지 UI 목업"
domain=design … model=gemini/gemini-3.5-flash

$ effi route --compact "UI 문자열 40개 번역"
domain=bulk … model=local/<ram-picked> cost=free
```

### 쿼터, 계정 & 로컬 폴백

**대부분 사용자:** Claude Code 로그인 유지 → 플랜 한도에 걸리면 `effi local` (Ollama). API 키 없음.

```sh
effi                  # 구독 / 플랜 로그인
# … 사용량 한도 …
effi local            # 무료 로컬 백엔드 (소형 모델용 MCP 차단)
effi pick --task "대량 번역"
effi run -t "번역" "…"                 # 생성 (stdout)
effi edit path.py "타입 힌트 추가"      # 재작성 → path.py.effi-new + diff
effi edit --apply-only path.py          # 검토 후 수락
```

**선택 — 다중 계정 / API 로테이션** (키가 있거나 프로필을 나눌 때만):

```sh
effi accounts init
effi accounts threshold 80
# api_key_env 내보내기  또는  계정별 oauth_profile config_dir
effi accounts meter work-primary 72
effi                             # 임계 미만 계정 선택
```

자세한 내용: [`docs/accounts.md`](docs/accounts.md).

> **약관:** 구독 OAuth를 제3 라우터/프록시에 넣지 마세요.  
> 정직한 토글(`effi` ↔ `effi local`) 또는 공식 다중 로그인·API 설정을 쓰세요.

### 철학 (짧게)

1. 코딩에서 멀티에이전트는 종종 토큰 **~15배** — 강한 리드 하나, 진짜 읽기/리뷰만 병렬 ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).  
2. **쓰기는 싱글스레드**; 리뷰는 클린 컨텍스트 ([Cognition](https://cognition.com/blog/multi-agents-working)).  
3. 절감은 **라우팅 + 프롬프트 캐시 연속**으로 — 리드를 몰래 약하게 만들지 말 것.  
4. 로컬은 **백스톱**이지 아키텍트가 아님.

---

## 명령어

| 명령 | 용도 |
|------|------|
| `effi` / `effi cloud` | Claude Code 세션 (모드 배너 + 계정 선택) |
| `effi local` | Ollama 위 Claude Code (소형 모델용 MCP 차단) |
| `effi mode …` | Apex·Cruise·Sip 표시 / 설정 / 질문 / 점검 |
| `effi route "…"` | 업무 → 모델 (모드 반영; 중요도 불일치 시 질문) |
| `effi use "…"` | 라우팅 + 실행 방법 (`--exec` 시 Claude) |
| `effi init` | 프로젝트 연결: `tasks/`, `CLAUDE.md`, `.effi/` |
| `effi doctor` | 상태 점검 |
| `effi new <이름> [목표]` | **프로젝트 루트** 아래 작업 폴더 생성 |
| `effi log <이름> <TAG> <메시지>` | `tasks/<이름>/log.md`에 append |
| `effi review [-o 디렉터리]` | 클린 컨텍스트 리뷰 팩 (diff + brief) |
| `effi pick` / `effi run` | 로컬 모델 선택 / 기계 **생성** 워커 |
| `effi edit <파일> "…"` | 로컬 **파일 재작성** → `.effi-new` 사이드카 + diff |
| `effi accounts …` | 다중 계정 임계 로테이션 |
| `effi catalog …` | 2주 주기 모델 카탈로그 상태 / 리서치 / bump |
| `effi status` | 스냅샷: 모드, 프로젝트, Ollama, 계정 |

```sh
effi help
```

---

## 저장소 구조

| 경로 | 내용 |
|------|------|
| [`bin/`](bin/) | CLI 진입점 (`effi`, `effi-mode`, `effi-route`, …) |
| [`lib/effi_core.py`](lib/effi_core.py) | 라우팅, 모드, 계정, doctor, 프로젝트 루트 |
| [`catalog/`](catalog/) | `models.json`, `task-routing.json`, `modes.json` |
| [`config/`](config/) | 계정·설정 예시 (시크릿 없음) |
| [`templates/`](templates/) | `task`, `log`, `brief`, `review`, `handoff` |
| [`docs/`](docs/) | 근거, 도메인, 계정 가이드 |
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | 리드 에이전트 운영 규칙 |
| [`CLAUDE.md`](CLAUDE.md) | 아무 프로젝트에 물리는 세션 규칙 |
| [`tests/`](tests/) | 단위 테스트 (라우팅, 모드, 중요도) |
| [`setup.sh`](setup.sh) | macOS 중심 부트스트랩 |

> 작업 산출물은 이 툴킷 clone 안이 아니라, **앱 쪽** `tasks/` 에 둡니다 (`effi init` / `effi new`).

---

## 문서

| 문서 | 설명 |
|------|------|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | 5단계 루프, 모드, 게이트 |
| [`ROUTING.md`](ROUTING.md) | 비용 규율 & 캐시 함정 |
| [`CLAUDE.md`](CLAUDE.md) | 세션 규칙 포인터 |
| [`FALLBACK.md`](FALLBACK.md) | 쿼터 → 로컬 토글 |
| [`LOCAL-MODELS.md`](LOCAL-MODELS.md) | 로컬 사다리 & RAM 규칙 |
| [`SETUP.md`](SETUP.md) | 세팅 가이드 |
| [`docs/why.md`](docs/why.md) | 연구 출처 |
| [`docs/domains.md`](docs/domains.md) | 기획 → 배포 파이프라인 |
| [`docs/accounts.md`](docs/accounts.md) | 다중 계정 설정 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 카탈로그 갱신 & PR 규칙 |
| [`CHANGELOG.md`](CHANGELOG.md) | 릴리스 이력 |

---

## 개발

```sh
export PATH="$PWD/bin:$PATH"
export PYTHONPATH="$PWD/lib"

python3 -m unittest discover -s tests -v
effi doctor
effi route --compact "레이트리밋 미들웨어와 단위 테스트 추가"
effi mode list
```

CI는 푸시/PR마다 동일 테스트를 실행합니다: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## 기여

[`CONTRIBUTING.md`](CONTRIBUTING.md)를 보세요.

요약:

- 모델 ID는 **공식 프로바이더 문서**만 (`effi catalog research`).  
- 후퇴 금지: 싱글 라이터, 메인 스레드 캐시 고정, 클린 컨텍스트 리뷰, 프로젝트 루트 tasks.  
- 실제 API 키는 커밋하지 말 것 (`~/.config/effi/accounts.json`은 로컬 전용).

---

## 솔직한 한계

- 로컬 모델은 어려운 멀티파일 작업에서 클라우드를 따라가지 못합니다.  
- `effi-edit`는 큰 파일을 거부합니다(기본 8 k자) — 작은 모델이 조용히 잘리지 않게.  
- 구독 사용자는 로컬로 **직접** 전환합니다 (`effi local`) — 약관 준수.  
- 바쁜 16 GB 맥은 작은 로컬 모델이 한계입니다 — 천장은 도구가 아니라 메모리.  
- Claude Agent Teams는 실험·고비용 — XL만.  
- 카탈로그 ID·가격은 변합니다 — 약 14일마다 재확인.

---

## 라이선스

1차 코드는 **Apache License, Version 2.0** 입니다 — [`LICENSE`](LICENSE) / [`NOTICE`](NOTICE).

Copyright © 2026 AscendraAI.
