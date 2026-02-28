# Service Catalog

## 서비스별 역할

## 1) `api_gateway` (기본 포트 8000)

- 현재 공통 `healthz/readyz` 중심의 경량 서비스
- 향후 외부 진입점으로 확장 가능한 위치

## 2) `conversation_orchestrator` (기본 포트 8001)

대화 중심 비즈니스 로직을 담당합니다.

주요 기능:

- 이벤트 append (`/internal/events/append`)
- 스냅샷 재구축 (`/internal/snapshots/rebuild/{conversation_id}`)
- 대화 시작(자동/수동)
- 턴 루프 실행 및 검증/거절/승인
- 사람 개입(intervention), 참여자 역할/moderation
- 메시지/이벤트/운영요약 조회 및 다운로드
- 컨텍스트 패킷 조립

## 3) `agent_runtime` (기본 포트 8002)

- `/internal/agents/run` 단일 엔드포인트 중심
- `agent_key` 기반 모델 라우팅
- 실행 결과를 표준 포맷(run id, token 추정, latency 등)으로 반환

## 4) `data_ingestion` (기본 포트 8003)

문서 파이프라인 시작점입니다.

주요 기능:

- 소스 업로드 (`/internal/sources/upload`)
- 소스 목록 페이지 조회 (`/internal/sources/page`)
- 텍스트 파싱/청킹 (`/internal/sources/{source_id}/process`)
- 임베딩 생성 (`/internal/sources/{source_id}/embed`)
- 토픽 클러스터링 (`/internal/sources/{source_id}/topics`)

## 5) `topic_pipeline` (기본 포트 8004)

- 현재 스캐폴드 성격의 서비스
- 추후 토픽 관련 독립 파이프라인 분리 지점

## 6) `export_service` (기본 포트 8005)

대화 결과 export와 버전 관리를 담당합니다.

주요 기능:

- export job 생성 (`/internal/exports/jobs`)
- job 조회/페이지 조회/파일 다운로드
- conversation 기준 dataset version 목록/상세/latest/다운로드

## 7) `scheduler` (기본 포트 8006)

자동화 템플릿과 실행 이력을 담당합니다.

주요 기능:

- 템플릿 생성/조회/목록/수정/enabled 토글
- 단일 실행 trigger/execute/preview
- 배치 execute
- 실행 이력 조회, run 상세 조회, retry

## 공통 포인트

- 모든 서비스는 `GET /healthz`, `GET /readyz` 제공
- 공통 미들웨어에서 인증/권한/요청 ID 처리
- 환경 변수 기반 포트/호스트 설정 (`SERVICE_HOST`, `*_PORT`)
