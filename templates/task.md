# [작업명]

```yaml
status: pending        # pending → in_progress → done | blocked
created: <YYYY-MM-DD>
grade: M               # S | M | L | XL  (ORCHESTRATION.md TRIAGE)
domain: implement      # from `effi route`
```

## 목표
<!-- 한 문장. 무엇을 완료로 볼 것인가. -->

## 제약 / 하지 말 것

## 라우팅 계획
```yaml
# catalog/task-routing.json · effi route
primary: claude/claude-sonnet-5
start_tier: mid        # local | cheap | mid | top
escalate_if: "검증 2회 실패 또는 애초에 어려운 판단"
workers_used: []
review: clean_context  # none | clean_context | clean_context+integration
main_thread_lock: claude
```

## 완료 기준
- [ ] 
- [ ] 검증(테스트/리뷰) 통과

## 메모
<!-- 계획 스케치. L/XL은 여기 길게. -->
