# effi-code

**v4.3 — 멀티 프로바이더 + 3모드 오케스트레이션.**  
🚀 **Apex** (최고 성능) · 🛣 **Cruise** (성능+비용) · ☕ **Sip** (최소 비용).  
언제든 `effi mode set …` 로 전환. 업무마다 Claude / Codex / Gemini / Grok / Local 중 최적 라우팅.

[English](README.md) | **한국어**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) ![Orchestration](https://img.shields.io/badge/orchestration-v4-green)

## v4가 푸는 네 가지

1. **태스크별 최적 모델** — 토큰·비용 최소 (`effi route`)  
2. **Claude 다중 계정** — 사용량 ≥ X% 시 전환, X는 사용자 설정  
3. **로컬 동적 추천** — 업무+RAM 기준, 2주 카탈로그 리뷰  
4. **기획→배포 전 구간** — 동일 루프·도메인 매트릭스  

## 시작

```bash
git clone https://github.com/AscendraAI/effi-code && cd effi-code
./setup.sh
export PATH="$PWD/bin:$PATH"
ln -s "$PWD/CLAUDE.md" /내/프로젝트/CLAUDE.md

effi accounts init
effi accounts threshold 80
effi route "레이트리밋 미들웨어 + 테스트"
effi
```

## 핵심 루프

```
TRIAGE → PLAN → DO → VERIFY → SHIP
```

- 메인 스레드는 **Claude 고정**(프롬프트 캐시). 다른 사는 **격리 서브**에만.  
- 쓰기는 **싱글 라이터**. 리뷰는 **클린 컨텍스트**.  
- 대량 기계 작업은 확인 후 로컬 (`effi run`).

## 문서

| 파일 | 내용 |
|---|---|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | v4 운영 규칙 |
| [`ROUTING.md`](ROUTING.md) | 라우팅·캐시 |
| [`docs/domains.md`](docs/domains.md) | 기획~배포 |
| [`docs/why.md`](docs/why.md) | 근거·출처 |
| [`catalog/task-routing.json`](catalog/task-routing.json) | 도메인 매트릭스 |

## 라이선스

Apache-2.0. © 2026 AscendraAI.
