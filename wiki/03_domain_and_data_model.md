# Domain and Data Model

## 핵심 도메인

## Conversation 도메인

- `conversation`: 대화 메타(tenant/workspace/title/objective/status/start_trigger)
- `participant`: 대화 참여자(human/ai/system)
- `message`: 턴 단위 메시지(status: proposed/validated/rejected/committed)
- `event`: 대화 이벤트 append log (`conversation_id + seq_no` 유니크)
- `conversation_snapshot`: 읽기 최적화 스냅샷(상태/턴수/마지막 이벤트)

핵심 포인트:

- 대화 시작 시 `conversation.created` + `conversation.started` 이벤트가 시퀀스 1/2로 기록
- 이벤트 append는 optimistic sequence check로 충돌(409) 방지

## Source/Topic 도메인

- `source_document`: 원본 파일/메타/저장위치
- `source_chunk`: 파싱된 텍스트 청크
- `source_chunk_embedding`: pgvector 임베딩(현재 dim=128)
- `topic`: 소스별 클러스터 토픽
- `source_chunk_topic`: 청크-토픽 매핑(relevance)
- `ingestion_dlq`: 처리 실패 기록

핵심 포인트:

- 파이프라인은 `upload -> process -> embed -> topics`
- 실패는 DLQ에 적재되어 추후 분석/재처리 가능

## Scheduler 도메인

- `automation_template`: RRULE 기반 자동화 템플릿
- `automation_run`: 실행 이력

핵심 포인트:

- `(template_id, idempotency_key)` 유니크 제약으로 중복 실행 제어
- idempotency key는 `template_id + scheduled_for(분 단위 정규화)` 해시

## Export 도메인

- `export_job`: export 실행 결과(포맷/row_count/manifest/storage_key)
- `conversation_dataset_version`: conversation별 데이터셋 버전 이력

핵심 포인트:

- export job 생성 시 dataset version이 함께 생성
- export 완료 시 orchestrator의 `event` 테이블에 `export.completed` 라인리지 이벤트 추가

## 마이그레이션 구성

- `0001` 대화 코어 스키마 + `pgcrypto`
- `0002` conversation snapshot read model
- `0003` source document 저장
- `0004` source chunk + ingestion DLQ
- `0005` `pgvector` + source chunk embedding
- `0006` topic + chunk-topic mapping
- `0007` scheduler template/run
- `0008` export job + lineage 기반
- `0009` dataset versioning
- `0010` 쿼리 성능 인덱스

## 상태 모델(요약)

- `conversation.status`: draft/prepared/active/paused/completed/curated/versioned/archived
- `message.status`: proposed/validated/rejected/committed
- `automation_run.status`: triggered/duplicate/failed
- `export_job.status`: queued/running/completed/failed
