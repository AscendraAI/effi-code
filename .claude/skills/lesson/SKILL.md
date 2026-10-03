---
name: lesson
description: 고장을 고친 뒤 그 교훈을 CLAUDE.md 산문이 아니라 실행되는 검사로 남긴다.
disable-model-invocation: true
argument-hint: "<무슨 고장이었나 한 줄>"
---

# /lesson

고장: $ARGUMENTS

`CLAUDE.md`에 절을 덧붙이지 않는다(150줄 상한, `scripts/verify.sh`가 센다). 아래 순서로 한다.

1. **재현 테스트.** 그 고장을 다시 만들면 빨간불이 나는 `tests/test_*.py` 케이스를 쓴다. 테스트 머리 docstring에 「부수는 법」 한 줄.
2. **종류를 묻는다.** 「이 한 건이 아니라 이런 종류 전체를 기계가 잡을 수 있나.」
   - 저장소 전체에 거는 정합(VERSION·CHANGELOG·카탈로그 형식 등)이면 `scripts/verify.sh` 4절에 한 단계
   - 편집 순간에 잡을 수 있으면 `.claude/hooks/check-edited.sh`
   - 실제 CLI 동작이면 `scripts/verify.sh` 5절 스모크에 한 줄 (CI도 같은 스크립트를 돈다)
3. 잡을 수 있으면 검사를 추가한다. **실패 메시지에 고치는 법을 적는다.** 추가한 검사는 **실제로 부숴서 빨간불을 본 뒤** 되돌린다 — 한 번도 빨간불을 못 본 검사는 검사가 아니다.
4. 못 잡으면 `CONTRIBUTING.md`의 「Design constraints」에 **한 줄**. 명령형으로, 경위는 빼고.
5. 경위가 길면 `docs/`에 남긴다. `grep` 되면 된다.
6. `bash scripts/verify.sh` — 초록불 출력을 붙여서 끝낸다. exit 2(점검 불가)는 통과가 아니다.
