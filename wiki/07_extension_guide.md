# Extension Guide

## 1) 새 내부 API 추가

권장 순서:

1. `services/<service>/app.py`에 request/response 모델 정의
2. 서비스 계층(예: `*_service.py`, `repository.py`)에 비즈니스 로직 분리
3. role/scope enforcement 적용
4. 테스트 추가 (`tests/test_*`)

가이드:

- DB 접근 로직은 가능한 repository 레이어로 격리
- API 핸들러는 검증/인가/매핑 중심으로 유지

## 2) 새 이벤트 타입 도입

권장 순서:

1. 이벤트 payload 스키마 정의(문서화)
2. append 지점 구현 (`event` insert)
3. snapshot/read model 영향 분석 및 반영
4. 이벤트/다운로드/히스토리 API와 호환성 확인

주의:

- 이벤트는 append-only 특성을 유지
- seq_no 충돌은 optimistic check로 처리

## 3) ingestion 단계 확장

가능한 확장 포인트:

- 파서 종류 확장(text 외 포맷)
- 임베딩 모델 교체/증설
- topic 클러스터링 전략 고도화
- DLQ retry 워커 추가

주의:

- 현재 스키마는 `embedding_dim=128` 제약을 가짐
- 차원 확장 시 마이그레이션과 인덱스 전략 재설계 필요

## 4) scheduler 확장

가능한 확장 포인트:

- run 상태 세분화(triggered/running/succeeded/failed)
- 재시도 정책 고도화(backoff, deadletter)
- external cron/queue와의 통합

주의:

- idempotency key 규약을 바꾸면 기존 run 중복 제어에 영향

## 5) export 확장

가능한 확장 포인트:

- parquet 실구현
- 원격 object storage(S3/GCS) 백엔드
- manifest schema version 업그레이드

주의:

- versioning 규약(`conversation_id + version_no`)은 유지 권장
- lineage 이벤트와 다운로드 API 계약을 함께 업데이트

## 6) 공통 규칙

- 모든 변경은 role/scope/trace 관점까지 검증
- keyset pagination API는 cursor 형식 안정성 유지
- 문서 변경 시 `README.md`와 `wiki/` 동시 반영 권장
