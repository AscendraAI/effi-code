# 세팅 가이드 v4

목표: **멀티 프로바이더 라우팅 + Claude 계정 로테이션 + 동적 로컬 + 기획~배포 루프**.

## 1. 설치

```bash
./setup.sh
export PATH="$PWD/bin:$PATH"
```

## 2. 세션 규칙

```bash
ln -s /path/to/effi-code/CLAUDE.md /your/project/CLAUDE.md
```

## 3. 멀티 Claude 계정 (선택)

자세한 가이드: [`docs/accounts.md`](docs/accounts.md)

```bash
effi accounts init
# ~/.config/effi/accounts.json 편집
# api_key_env 에 대응하는 환경변수 export
export ANTHROPIC_API_KEY_WORK=sk-ant-...
export ANTHROPIC_API_KEY_WORK2=sk-ant-...

effi accounts threshold 80    # 사용량 80% 이상이면 다음 계정
effi accounts meter work-primary 0
effi accounts select
effi doctor                   # credentials resolvable > 0 확인
```

`effi`(cloud) 실행 시 threshold 미만 계정을 자동 선택하고 env를 적용합니다.

## 4. 로컬 Ollama

```bash
ollama serve
# 모델은 필요 시 자동 pull 또는:
ollama pull qwen2.5-coder:7b
effi pick --task "번역"
```

## 5. 프로젝트에 연결 (v4.1)

```bash
cd /path/to/your-app
effi init                 # tasks/ + CLAUDE.md → toolkit
effi doctor
effi new my-feature "목표"  # tasks/ 는 앱 루트에 생성
```

## 6. 일상

```bash
effi route "할 일 설명"     # 모델 결정
effi new my-feature "목표"
effi                        # Claude 세션
effi catalog status         # 2주 갱신 여부
python3 -m unittest tests/test_route.py   # 툴킷 개발 시
```

## 검증 체크

- [ ] `effi doctor` overall OK
- [ ] `effi route "architecture redesign"` → opus
- [ ] `effi route "translate strings"` → local
- [ ] `effi new` 가 **앱 루트** `tasks/` 에 생성 (툴킷 clone 안)
- [ ] `effi accounts threshold 80` 동작
- [ ] `effi catalog status` stale=false
