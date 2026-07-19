# 설계 근거 (딥리서치 요약)

2026-07 딥리서치(28소스·134주장·22확정). 이 설계가 왜 이런지의 근거.

## 오케스트레이션
- **강모델 오케스트라 + 싼 서브 = 유효** — Anthropic: Opus 리드 + Sonnet 서브가 단일 Opus를 **90.2% 능가**. 단 *리서치* 태스크.
- **코딩에선 멀티에이전트 절제** — 멀티는 **토큰 ~15배**(에이전트 ~4배), 고가치에서만 정당화, 대부분 코딩엔 부적합.
- **[arXiv] 동일 예산이면 단일 ≥ 멀티** (3모델·5아키텍처). 멀티 이득 상당수는 구조 아닌 추가 연산·컨텍스트.
- **[Cognition/Devin] 멀티에이전트 짓지 마라** — 단일 스레드 + 연속 컨텍스트, 쓰기는 싱글스레드. 멀티 변형 39~70% 저하 가능.
- **강+약(로컬) "스마트 프렌드"는 미해결** — 둘 다 강할 때만 작동. → 로컬을 핵심 워커로 쓰지 말 것.
- **검증이 새 병목**(생성 아님) → 필수 게이트. 리뷰 에이전트 PR당 ~2버그(58% 심각).

## 비용
- **라우팅 1순위** — 클로드코드 토큰 **85% Opus 불필요** → $200→$30/월. RouteLLM 강모델 14~26%만, 품질 95%. 실사용 **62% 절감**.
- **캐싱** — 읽기 90% 할인이나 80% 히트율서 실제 **~32%**. 배치 결합 시 최대 95%.
- ⚠️ **라우팅↔캐싱 충돌** — 프로바이더 파편화 시 클로드 캐시 깨져 **10배**. 캐시 킬러: 가변 시스템 프롬프트·슬라이딩 윈도우.

## 폴백 인프라
- **claude-code-router**(30k★): 순서 폴백 체인 + 재시도, Ollama 포함 다중 프로바이더.
- **LiteLLM**: 자동 페일오버. **OpenRouter**: models[] + 프로바이더 페일오버.
- **Ollama v0.14+ Anthropic API 네이티브** → 로컬이 클로드 백엔드로 직결.

## 로컬 현실
- 일상 ~80% 커버, 최난도 불가, 2~10배 느림. 계획 Opus 9.8 vs 로컬 6.3. 자가호스팅=비용책 아님, 백스톱용.

## 하네스
- Cline·Aider·Goose·Kilo·OpenHands·OpenCode 전부 BYOK + Ollama 지원(락인 없음). Claude Code는 서브에이전트·워커별 모델·5단계 압축·fallbackModel 내장.

## 주요 출처
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://code.claude.com/docs/en/agent-teams
- https://arxiv.org/pdf/2604.02460 (단일≥멀티, 동일예산)
- https://cognition.com/blog/multi-agents-working
- https://github.com/musistudio/claude-code-router
- https://docs.litellm.ai/docs/proxy/reliability
- https://ofox.ai/blog/claude-code-hybrid-routing-pattern-2026/
- https://ofox.ai/blog/prompt-caching-cost-math-anthropic-vs-openai-2026/
- https://johnhringiv.com/claude-vs-local
