# Plan — effi-code v5 방향

> Status: **방향 확정 · 가설 검증 전** (2026-10-03)
> 이 문서는 방향과 그 근거만 다룬다. 코드는 아직 바꾸지 않았다.
> 근거 수집: 로컬 실측(Orca 1.4.217 · Claude Code 2.1.281 · codex 0.157.1 · gemini 0.54.4) + 웹 조사 3회. 출처는 6절.

---

## 1. 한 줄

**effi = 사용자 대신 AI 생태계를 따라가는 작업환경 관리자.**
오케스트레이션 엔진은 갖지 않는다. 어느 런타임에서든 **같은 정책**을 적용하고,
일반 터미널에서는 Claude가 다른 회사 모델을 부를 수 있게 **다리**가 된다.

## 2. 사용자 결정 (2026-10-03)

| # | 결정 |
|---|---|
| D1 | 직접 쓰면서 **공개 제품**으로 만든다. 사업화까지 고려한다 |
| D2 | Orca 같은 오케스트레이션 IDE 안에서는 **협업**한다. 일반 터미널에서는 **여러 회사 모델 위임**을 effi가 맡는다 |
| D3 | 실행 엔진(세션 · 워크트리 · 병렬)은 만들지 않는다. IDE 것이나 Claude Code 기본 기능을 빌린다 |

## 3. 세 기둥 + 다리

| | effi가 하는 일 | 하지 않는 일 |
|---|---|---|
| ① 도구 | 스킬 · MCP · 플러그인 · CLI 목록을 레지스트리에서 **자동 수집**하고, 작업에 맞게 추천 → **승인 후에만** 설치 · 버전 고정 · 업데이트 때 설명 diff | 손으로 큐레이션 · 자동 설치 |
| ② 하네스 | 프로젝트에 맞는 verify · 훅 · CI · 규칙 · 권한을 세팅하고, **일부러 부숴 빨간불을 확인**한 것만 「섰다」고 한다. 작업이 바뀌면 다시 추천 | 깔기만 하고 안 돌리기 |
| ③ 사용량 | 한 회사 **안에서** 모델 등급 · effort · 캐시 최적화. 보조 작업은 API 키로 다른 회사에. 사용량 수치는 Orca(`orca account list --json`) · ccusage에서 **읽는다** | 사용량 측정 재구현 · 구독 OAuth 중계 |
| 다리 | `effi delegate` — 어느 회사에 맡길지 **판단 · 격리 · 관문 · 기록**만 한다. 실행은 공식 플러그인 · CLI(codex-plugin-cc · `codex exec` · `gemini --acp` · `ollama`)에 맡긴다. **경로 + 짧은 요약**만 반환, verify 통과 뒤 합침 | 위임 실행기 직접 구현 · 메인 스레드를 다른 회사로 옮기기 |

```
                 effi = 정책층 (무엇을 · 어떤 조건으로)
   모델 · effort · 승인된 도구 목록 · verify 관문 · 동시 실행 상한 · 계보 · 게이트
                          │ 같은 정책
        ┌─────────────────┼──────────────────────┐
     Orca 안             일반 터미널              그 외 IDE
   orca task/worker    Claude Code 기본 기능      플러그인 · 훅만으로
   (ORCA_* 환경변수로    (--bg · agents · -w ·
    감지)                Workflows · 서브에이전트)
                          + effi 다리(MCP) → codex · gemini · grok · ollama
```

## 4. 근거 — 왜 이 모양인가

### 4.1 엔진을 만들지 않는 이유
- Claude Code가 일반 터미널에서 이미 오케스트레이션한다: `--bg` · `claude agents --json` · `-w` · `--tmux` · 서브에이전트 · Dynamic Workflows(2026-05-28~) · Teams(실험) · 클라우드 세션 GA(09-23) · Projects 베타(09-17). 1년에 다섯 번 나왔다.
- IDE 층은 붐빈다: Orca(★~8.4만 · 무료) · Conductor($24.5M) · Cursor 3 · Codex 앱 · Antigravity 2.0.
- 오케스트레이터 사이의 표준은 없다(ACP는 편집기↔에이전트). **모든 런타임이 공통으로 읽는 것** — 플러그인 · 훅 · MCP 설정 · 스킬 · `AGENTS.md` — 에 정책을 실으면 어댑터를 IDE마다 만들 필요가 없다.

### 4.2 그래도 터미널에 다리가 필요한 이유
- Claude Code의 기본 오케스트레이션은 **Claude 모델만** 부린다(서브에이전트 · `--bg`의 모델 선택지가 Opus/Sonnet/Haiku).
- effi도 아직 이 자리를 못 채웠다: `effi route`는 추천만 하고, 실제 위임(`effi run`/`edit`)은 **로컬 Ollama 전용**이다. 다른 회사 CLI는 이 기기에 다 깔려 있다.
- MCP로 만들면 서브에이전트 · Workflows 스크립트 · Orca 안의 claude 워커가 **같은 도구**를 부른다 → 환경이 달라도 정책이 같다.

### 4.3 단순 추천만으로는 차별이 없다
- Anthropic 공식 `claude-code-setup` 플러그인이 저장소를 훑어 MCP · 스킬 · 훅 · 서브에이전트를 추천한다(로컬 marketplaces에서 확인).
- Vercel `find-skills` 설치 300만+ · Smithery Toolbox · MCP Compass.
- Claude Code가 도구 스키마를 필요할 때만 불러온다(기본값).
- → 이것들은 **데이터 소스로 쓰고** 경쟁하지 않는다. 남은 자리는 **신뢰층 · 지속 재평가 · 하네스 실증**.

### 4.4 신뢰층이 차별점인 이유
- postmark-mcp(2025-09): 정상 15버전 뒤 16번째에서 메일을 몰래 BCC.
- Snyk ToxicSkills(2026-02): 스킬 3,984개 중 36% 결함 · 76개 악성.
- CVE-2025-54136(승인 뒤 도구가 바뀌는 rug pull).

### 4.5 사업화
- 개인은 거의 안 낸다(레지스트리 유료판 월 $9~10 · Smithery는 Arcade가 인수 · 오케스트레이터 로컬 무료).
- 팀은 **거버넌스**에 낸다: Runlayer 시리즈A $30M(2026-06 · 승인 워크플로 · 감사 · 주입 탐지) · Kong AI Gateway 2.2(09-30) · Cloudflare MCP Portals.
- effi의 틈: 그들은 **게이트웨이 인프라**로 MCP 트래픽만 통제한다. effi는 **저장소 안의 파일**로 하네스 전체를 관리한다 — 서버 없이 git으로, 5~50명 팀.

| 판 | 내용 |
|---|---|
| 무료 (Apache-2.0 유지) | 추천 · 승인 후 설치 · 하네스 세팅 · 사용량 조언 · 다리. 개인용 전부 |
| 유료 (팀) | 조직 공통 허용목록 · 정책 동기화 · 도구 잠금파일 · 감사 로그 · 팀 비용 리포트 |

## 5. 지켜야 할 선

- **구독 OAuth를 중계하지 않는다.** 사용자가 직접 로그인한 수정 안 된 공식 바이너리만 부른다(Anthropic legal-and-compliance의 예외 조항 · Orca · Conductor와 같은 형태). Agent SDK는 API 키로만.
- **Gemini는 API 키.** 개인 로그인은 2026-06-18에 막혔다.
- 제품 이름에 「Claude Code」를 쓰지 않는다.
- 메인 스레드는 Claude 고정(캐시). 다른 회사 결과는 사이드카 · 워크트리로만, verify 통과 뒤 합친다.

## 6. 증명할 가설

| # | 가설 | 실험 | 실패하면 | 상태 |
|---|---|---|---|---|
| H1 | effi 추천이 `claude-code-setup`보다 낫다 | 내 프로젝트 3개에서 나란히 비교 | 추천을 접고 신뢰층 · 하네스로 | **완료 — 「낫다」는 기각, 빈자리 4개 확인**(6.2). 추천 엔진은 공식을 부르고, effi는 판정 바닥 · 신뢰 · 지속 · 비용을 얹는다 |
| H2 | 신뢰층이 필요하다 | 지금 설치된 플러그인 · 스킬 · MCP 감사 | 유료 근거 재검토 | **1차 근거 있음** — 6.1 |
| H3 | 하네스가 다른 스택으로 옮겨진다 | 쓰는 저장소에 `effi harness`로 이식 | 스택별 템플릿 | **2개 스택 성공** — Python(picknow-ops → effi-code 손 이식 · sg-beauty 사본 증명 6/6) · **Node/Next(picknow-homepage 실제 적용, 증명 4/4 · 타입 오류까지 잡음, 2026-10-03)** |
| H4 | 같은 정책이 Orca와 일반 터미널에서 똑같이 동작한다 | 같은 점검을 터미널(`claude -p -w`) · Orca Claude 워커 · Orca Codex 워커에 | 어댑터 재설계 | **완료 — 런타임은 같다, 에이전트 종류가 갈린다**(6.3) |
| H5 | 팀이 거버넌스에 돈을 낸다 | 개발팀 리드 5명 인터뷰 또는 대기자 명단 | 개인용 무료로만 | 대기 (사람이 해야 함) |
| H6 | 다른 회사로 가는 다리가 이미 있다 | 기존 MCP 브리지 조사 | — | **완료** — 실행은 있고 판단은 없다(7절) |

### 6.1 H2 1차 — 이 기기 실측 (2026-10-03, 읽기 전용)

| 발견 | 왜 위험한가 |
|---|---|
| MCP 서버 하나가 `npx -y <패키지>@latest`로 뜬다 | 켤 때마다 **최신판을 받아 실행**한다. postmark-mcp가 정확히 이 경로(정상 15판 뒤 16판에서 악성)였다. 버전 고정이 없다 |
| 마켓 5곳 중 4곳이 서드파티 | Anthropic은 서드파티 마켓을 심사하지 않는다 |
| 서드파티 플러그인 하나가 **훅 22종**을 건다(PreToolUse · PermissionRequest · UserPromptSubmit 포함) | 모든 도구 호출 · 권한 요청 · 프롬프트에 코드가 끼어든다. 지금은 비활성이지만 설치돼 있다 |
| 스킬 12개가 `~/.agents/skills` 심볼릭 링크 | 어디서 어느 버전을 받았는지 이 기기에 기록이 안 보인다 |
| 플러그인은 git SHA로 고정돼 있다 | ✅ 좋은 쪽. 신뢰층이 이 형식을 MCP · 스킬로 넓히면 된다 |

→ 개인 개발 기기 하나에서도 신뢰층이 잡을 것이 나온다. 「몇 명이 이런 상태인가」는 H5에서 같이 묻는다.

### 6.2 H1 — 공식 `claude-code-setup` 대 실제로 효과가 증명된 하네스 (2026-10-03)

**방법.** 세 저장소의 사본에 공식 추천기를 돌렸다. 개인 설정은 빼고(`--setting-sources project`), 읽기만 허용했다(Edit · Write · Bash 금지).
- picknow-ops는 **하네스를 만들기 전 시점**(`50077a3`, 09-18)으로 되돌렸다. 정답지는 그 뒤 나흘(09-19~23) 동안 실제로 지어 효과를 확인한 12가지다.
- effi-code는 오늘 하네스를 넣기 전(HEAD), sg-beauty는 지금 상태로 돌렸다.
- 원문: 스크래치패드 `h1/*.out.md`(저장소 밖).

**picknow-ops — 정답지 12개 중 얼마나 맞췄나**

| # | 실제로 지어 효과가 확인된 것 | 공식 추천 |
|---|---|---|
| 1 | `verify.sh` 단일 판정(0 통과 · 1 발견 · 2 점검 불가 ≠ 통과) | ✗ |
| 2 | pre-push 자동 관문(CI가 한 번도 안 돌았다) | ✗ |
| 3 | Stop 훅 verify(무인 실행 때만, 5회 상한) | ✗ |
| 4 | 편집 직후 고친 파일만 lint | ✓ |
| 5 | 이미 있는 마이그레이션 수정 차단 | △ 쓸 때 lint만 |
| 6 | 위험 명령 거부(`git add -A` · force push · `db push` · `.env` 읽기) | △ git 쪽만 |
| 7 | `AGENTS.md` 5,009줄 → 150줄 + 경로별 규칙 | ✗ 5천 줄을 **읽고 활용**했지만 길이는 문제 삼지 않았다 |
| 8 | 고장 → 검사로 바꾸는 절차(`/lesson`) | △ hookify |
| 9 | 테스트 바닥(pgTAP 12→98 · e2e 0→36) | ✗ |
| 10 | 화면 검증(ux-evaluator + Playwright) | ✓ Playwright MCP |
| 11 | 야간 정원사 리뷰 | △ 필요할 때 부르는 리뷰어만 |
| 12 | 무인 실행(`go.sh`) | ✗ |

→ **완전 일치 2 · 부분 4 · 놓침 6.** 놓친 6개 중 5개(1 · 2 · 3 · 7 · 9)가 같은 종류다 — **「결과를 기계가 판정하는 바닥」**. 에이전트 하네스에서 증거가 가장 강한 영역(4절)이다.

**공식 추천기가 더 잘한 것 — 정직하게**
- **도메인 맞춤 스킬**: `new-migration`(번호 충돌 4회 · 최신본 복사 사고를 체크리스트로) · `money-feature-preflight` · sg-beauty의 `ingest-quote` · `snapshot`. effi가 템플릿으로는 못 만드는 수준이다.
- **문맥 판단**: picknow 보안원장의 결정을 읽고 Supabase MCP를 **추천하지 않았다**.
- **effi-code에서 실제 문제를 찾았다**: 버전 불일치(VERSION 4.7.1 · README 4.4.5 · CLAUDE.md v4.3) · 카탈로그 재검토 기한 2달 지남(= 오늘 고친 `test_splash` 날짜 폭탄의 원인) · `.claude/` 통째 무시.

**공식 추천기에 없는 것 — effi의 자리**

| 빈칸 | 증거 |
|---|---|
| 판정 바닥 | 위 표의 놓침 5개. effi-code에는 「편집마다 전체 테스트」 훅을 권했다 — picknow가 「느리면 사람이 끈다」고 기각한 패턴이다. 이 저장소에서는 `.effi/mode` 누출로 늘 빨간불이었을 것이다(4건 실측) |
| 신뢰 | 세 보고서 중 둘이 `npx @playwright/mcp@latest`(버전 고정 없음)를 권했다 |
| 실증 | 권한 훅이 실제로 막히는지 부숴 보지 않는다. 한 줄짜리 `jq` 훅은 `jq`가 없는 기기에서 **조용히 통과**한다 |
| 지속 | 한 번 스캔하고 끝난다. 지식원은 사람이 쓴 고정 목록(MCP 약 25개 · 1,477줄)이다 |
| 비용 · 모델 | 세 보고서 모두 모델 · effort · 사용량 이야기가 **0줄**이다 |

**한계.** 표본이 3개이고 채점은 한 사람(이 세션)이 했다. Bash를 막아서 공식 추천기는 CI 상태(`gh`)나 테스트를 실제로 돌려 볼 수 없었다 — 2번 「CI가 안 돈다」를 놓친 데에는 이 조건 탓도 있다.

**판정 — H1은 「effi가 낫다」로는 기각, 「effi가 채울 자리가 있다」로는 채택.**
1. **저장소를 한 번 훑어 추천하는 엔진은 만들지 않는다.** 공식 추천기가 충분히 좋다 — effi는 그것을 **부르고 그 결과를 받는다**.
2. effi는 그 위에 네 겹을 얹는다: ① **판정 바닥 팩**(verify 0/1/2 · pre-push · 격리 실행 · 부숴 보기 증명 — picknow → effi-code 이식으로 이미 두 번 실증) ② **신뢰 검사**(`@latest` 같은 미고정 설치를 고정판으로 바꿔 제시) ③ **지속 재평가**(CI가 빨간지 · 컨텍스트 파일이 길어졌는지 · 기한이 지났는지 실제로 돌려 본다) ④ **모델 · 사용량 조언**.

### 6.3 H4 — 정책은 런타임이 아니라 에이전트 종류에서 갈린다 (2026-10-03)

같은 점검 지시(모드 · `git add -A` 거부 · 편집 문법 훅 · 세션 컨텍스트 · 권한 모드)를 세 곳에 내렸다.

| | 일반 터미널 (Claude) | Orca (Claude) | Orca (Codex) |
|---|---|---|---|
| `git add -A` 거부 | ✅ 막힘 | ✅ 막힘 | ❌ 허용 |
| 편집 문법 훅 | ✅ 막힘 | ✅ 막힘 | ❌ 조용함 |
| effi 세션 컨텍스트 | ✅ | ✅ | ❌ |
| 권한 모드 | bypass | **bypass(Orca 기본값)** | 알 수 없음 |
| effi 모드 | ⚠️ Cruise (메인은 Apex) | ⚠️ Cruise | ⚠️ Cruise |

1. **Orca와 터미널은 같다.** bypass 모드에서도 거부 규칙은 지켜졌다 → 런타임 어댑터는 필요 없다.
2. **Codex 워커에는 effi 정책이 0이다.** 정책이 Claude Code 설정 형식에만 있기 때문이다. 모든 에이전트에 걸리는 것은 git 층의 pre-push뿐이었다 → **정책을 한 곳에 쓰고 에이전트마다 그 형식으로 내보내야 한다**(Claude: `.claude/settings.json` · Codex: `.codex/` 훅 · 공통: `AGENTS.md`), 아니면 git · verify 층에 둔다.
3. **모든 워크트리에서 모드가 풀렸다.** `.effi/mode`는 추적되지 않아 워크트리로 안 따라간다 → **고쳤다**: 링크된 워크트리는 메인 체크아웃의 고정을 이어받는다(`none`으로 끌 수 있다). Codex가 읽기 전용 리뷰로 결함 4건을 더 찾아 함께 고쳤다(공백 경로 · 옛 git · 서브모듈/bare · 해제 후 재상속).

## 7. H6 — 다리는 이미 있는가

**결론: 「다른 회사 모델을 부르는 것」은 이미 있다. 「어느 회사에 맡길지 정하는 것」은 아무도 안 한다.**

| 도구 | 어느 회사에 맡길지 판단 | 사용량 반영 | 쓰는 사람 1명 · 워크트리 격리 | 검증 관문 | 상태 |
|---|---|---|---|---|---|
| openai/codex-plugin-cc (공식) | ✗ | ✗ | 언급 없음 | Stop 훅 리뷰 게이트(선택) | ★3.4만 · 2026-07 v1.0.6 |
| PAL MCP (옛 zen-mcp) | ✗ (호출자가 지정) | ✗ | ✗ — 기본값이 승인 우회 · 동시 쓰기 | ✗ | ★1.2만 · **2025-12 이후 멈춤** |
| gemini-mcp-tool | ✗ | ✗ | ✗ | ✗ | ★2.3천 |
| claude-code-router | 요청 단위 백엔드 교체(위임 아님) | 일부 | — | — | ★3.75만 · 구독 OAuth 중계 위험 |
| Orca · Conductor | 사람이 고름 | ✗ | ✓ 워크트리 | ✗ | — |

- `codex mcp-server`는 **2026-09-05에 제거됐다**(로컬 codex 0.157.1 도움말에도 없다). 그 위에 지은 래퍼들이 같이 흔들린다 → 실행 경로를 직접 소유하면 이런 변화를 혼자 따라가야 한다.
- Gemini CLI는 MCP 서버 모드가 아니라 `gemini --acp`(ACP)로 외부에서 부른다.

**결정: 위임 실행기는 만들지 않는다. 있는 것을 감싼다.**

```
effi delegate (MCP 도구 하나 · 얇게)
  ① 판단   모드(Apex/Cruise/Sip) + 남은 사용량(Orca · ccusage) + 작업 종류 → 회사 · 모델
  ② 격리   위임받은 쪽은 자기 워크트리 · 사이드카에만 쓴다
  ③ 실행   codex-plugin-cc / codex exec · gemini --acp · agy · ollama   ← 남의 것
  ④ 관문   verify 통과 전에는 합치지 않는다 (codex-plugin-cc의 Stop 훅 게이트 패턴을 빌림)
  ⑤ 기록   경로 · 요약 · 비용을 원장에
```

- 실행기는 effi가 **추천 · 승인 · 버전 고정하는 도구 목록의 일부**가 된다 — ① 도구 기둥과 같은 장치로 관리한다.
- PAL의 `consensus`(여러 모델 토론)는 Apex 모드 선택 기능으로만 검토한다. 토대로 삼지 않는다(유지보수 멈춤).

출처(2026-10-03): https://github.com/openai/codex-plugin-cc · https://github.com/BeehiveInnovations/pal-mcp-server · https://learn.chatgpt.com/docs/mcp-server · https://geminicli.com/docs/cli/acp-mode/ · https://github.com/musistudio/claude-code-router

## 8. 열린 질문

- ~~**Q1**~~ **결정(2026-10-03): 권장대로.** 자동 로테이션은 API 키 계정만. 구독 OAuth 프로필은 `effi accounts select --id`로 직접 고를 때만 쓴다(`tests/test_accounts.py`).
- ~~**Q2**~~ H1 완료(6.2). 다음 후보: H4(같은 정책이 Orca와 터미널에서 똑같이 도나) 또는 첫 구현(판정 바닥 팩).
- ~~**Q3**~~ **해결(2026-10-03).** `test_splash` 2건은 실제 버그였다(이어 열기 때 온보딩 전체 노출 · 경고 길이 무제한 → 날짜가 지나며 터짐). 고쳤고 `scripts/verify.sh` 전체 초록(170건). pre-push 훅을 켤 수 있다.

## 9. 출처 (2026-10-03 확인)

- Claude Code 이용약관 — https://code.claude.com/docs/en/legal-and-compliance
- Claude Code agent view — https://code.claude.com/docs/en/agent-view
- Dynamic Workflows — https://www.infoq.com/news/2026/06/dynamic-workflows-claude-code/
- Claude Code Projects — https://venturebeat.com/orchestration/anthropic-launches-claude-code-projects-an-always-on-conversation-that-remembers-and-delegates-your-long-running-dev-work
- claude-code-setup — https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-code-setup
- MCP Registry — https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/
- Agent Skills 표준 — https://agentskills.io/specification
- skills.sh find-skills — https://www.skills.sh/vercel-labs/skills/find-skills
- postmark-mcp — https://thehackernews.com/2025/09/first-malicious-mcp-server-found.html
- Snyk ToxicSkills — https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/
- Runlayer 시리즈A — https://www.hpcwire.com/aiwire/2026/06/25/runlayer-raises-30m-series-a-to-help-enterprises-become-ai-native/
- Arcade × Smithery — https://www.forbes.com/sites/janakirammsv/2026/08/10/arcade-acquires-smithery-to-own-the-agent-tool-supply-chain/
- AGENTS.md 효과 연구 — https://arxiv.org/abs/2602.11988 · https://arxiv.org/abs/2601.20404
- 하네스 엔지니어링 현황 — https://marmelab.com/blog/2026/09/24/the-state-of-ai-harness-engineering-2026.html
