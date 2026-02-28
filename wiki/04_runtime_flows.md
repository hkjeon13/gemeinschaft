# Runtime Flows

## 1) Ingestion Flow

1. `POST /internal/sources/upload`
2. `POST /internal/sources/{source_id}/process`
3. `POST /internal/sources/{source_id}/embed`
4. `POST /internal/sources/{source_id}/topics`

핵심 처리:

- 업로드: 파일 저장 + 체크섬/메타 저장
- process: 텍스트 파싱 후 chunk 단위 분할
- embed: chunk 임베딩 업서트
- topics: 임베딩 기반 클러스터링 후 topic/link 저장

실패 처리:

- 각 단계 실패 시 `ingestion_dlq`에 error_type/error_message/payload 기록

## 2) Conversation Start Flow

### 자동 시작

- `POST /internal/conversations/start/automation`
- `automation_run_id` 기준 idempotent 재호출 지원(기존 conversation 반환)

### 수동 시작

- `POST /internal/conversations/start/manual`

공통 동작:

- conversation/participant 생성
- 이벤트 2개를 초기 시퀀스로 append
  - `conversation.created`
  - `conversation.started`

## 3) Loop Run Flow

- `POST /internal/conversations/{conversation_id}/loop/run`

요약 알고리즘:

1. conversation 상태/participants 로드
2. 최근 steering intervention/최근 메시지 로드
3. 턴마다 context packet 조립(선택)
4. 턴 텍스트 생성(내부 deterministic 또는 agent_runtime)
5. validator 통과 여부 판정
6. committed 혹은 proposed/rejected 경로 처리
7. 이벤트/메시지/stop reason 집계 반환

옵션 예시:

- `require_human_approval`: AI 턴을 `proposed` 상태로 대기
- `arbitration_enabled`, `pause_on_disagreement`
- `derailment_guard_enabled`, `min_topic_keyword_matches`

## 4) Human-in-the-loop Flow

- 개입: `POST /internal/conversations/{conversation_id}/interventions/apply`
- 승인/거절: 단건/배치 approval API
- 거절 큐, 대기 큐, 실패 요약 API로 운영자 리뷰 지원

## 5) Scheduler Flow

- 템플릿 생성 (`/internal/automation/templates`)
- 실행 trigger/execute (`/internal/scheduler/runs/trigger`, `/execute`)
- 필요 시 orchestrator 호출로 자동 대화 시작
- run 상태/에러를 실행 이력으로 추적

## 6) Export Flow

- `POST /internal/exports/jobs`
- conversation 메시지를 `jsonl` 또는 `csv`로 직렬화
- 파일 저장 + manifest 생성 + dataset version upsert
- `export.completed` 이벤트 append
- latest/version 별 다운로드 API로 조회
