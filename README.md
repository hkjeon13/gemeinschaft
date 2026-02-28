# gemeinschaft

`gemeinschaft`는 이벤트 기반 멀티-에이전트 대화 오케스트레이션 백엔드입니다.  
FastAPI 기반의 여러 내부 서비스가 함께 동작하며, 문서 수집/토픽화, 대화 실행, 자동화 스케줄링, 데이터셋 내보내기를 제공합니다.

## 프로젝트 개요

이 저장소는 아래 흐름을 중심으로 구성되어 있습니다.

1. 소스 업로드 및 처리: 파일 업로드 -> 텍스트 파싱/청킹 -> 임베딩 -> 토픽 클러스터링
2. 대화 실행: 자동/수동 대화 시작 -> 턴 루프 실행 -> 승인/거절/중재/개입 처리
3. 운영 자동화: RRULE 기반 템플릿 관리 및 스케줄 실행
4. 결과 내보내기: 대화 메시지를 버전된 데이터셋(JSONL/CSV)으로 export

모든 서비스는 공통적으로 `GET /healthz`, `GET /readyz`를 제공합니다.

## 서비스 구성

기본 포트(환경 변수로 변경 가능):

- `api_gateway` (`API_GATEWAY_PORT`, 기본 `8000`): 현재 스캐폴드(헬스체크 중심)
- `conversation_orchestrator` (`CONVERSATION_ORCHESTRATOR_PORT`, 기본 `8001`): 이벤트 저장, 대화 시작/루프, 승인/중재/운영 조회
- `agent_runtime` (`AGENT_RUNTIME_PORT`, 기본 `8002`): 에이전트 실행 래퍼 (`/internal/agents/run`)
- `data_ingestion` (`DATA_INGESTION_PORT`, 기본 `8003`): 업로드/처리/임베딩/토픽 API
- `topic_pipeline` (`TOPIC_PIPELINE_PORT`, 기본 `8004`): 현재 스캐폴드(헬스체크 중심)
- `export_service` (`EXPORT_SERVICE_PORT`, 기본 `8005`): export job/버전 관리/다운로드
- `scheduler` (`SCHEDULER_PORT`, 기본 `8006`): 자동화 템플릿/실행/재시도/배치 실행

## 저장소 구조

```text
.
├── services/
│   ├── api_gateway/
│   ├── conversation_orchestrator/
│   ├── agent_runtime/
│   ├── data_ingestion/
│   ├── topic_pipeline/
│   ├── export_service/
│   ├── scheduler/
│   └── shared/                 # 공통 app/auth/db 유틸
├── db/migrations/              # Postgres SQL 마이그레이션
├── scripts/
│   ├── dev.py                  # 멀티 서비스 로컬 실행기
│   └── migrate.py              # up/down/status 마이그레이션 러너
├── tests/                      # 서비스/리포지토리 테스트
├── docs/
│   └── BUILD_AND_DEPLOY.md     # 배포 가이드
├── Makefile
└── .env.example
```

## 빠른 시작 (로컬 실행)

### 1) 사전 준비

- Python `3.12+`
- PostgreSQL (`pgvector` extension 사용 가능해야 함)

### 2) 환경 변수 준비

```bash
cp .env.example .env
```

기본값은 로컬 개발에 맞춰져 있습니다. 특히 `DATABASE_URL`을 사용 가능한 DB로 맞춰주세요.

### 3) 가상환경/의존성 설치

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 4) DB 마이그레이션

```bash
make db-migrate-up
```

참고:

- 마이그레이션 중 `CREATE EXTENSION IF NOT EXISTS vector;`가 포함되어 있습니다.
- 권한 정책으로 extension 생성이 제한된 환경에서는 DBA 사전 설치가 필요합니다.

### 5) 전체 서비스 실행

```bash
make dev
```

자동 리로드가 필요하면:

```bash
make dev-reload
```

### 6) 상태 확인

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8001/readyz
curl -fsS http://127.0.0.1:8006/healthz
```

## Docker Compose 실행

### 1) 환경 파일 준비

```bash
cp .env.example .env
```

### 2) 전체 스택 실행

```bash
docker compose up --build -d
```

`migrate` 서비스가 먼저 실행되어 DB 마이그레이션을 적용한 뒤 앱 서비스들이 올라옵니다.

변수화된 주요 값(`.env`로 제어):

- `COMPOSE_DATABASE_URL`, `COMPOSE_POSTGRES_USER`, `COMPOSE_POSTGRES_PASSWORD`, `COMPOSE_POSTGRES_DB`, `COMPOSE_POSTGRES_PORT`
- `COMPOSE_SERVICE_HOST`
- `COMPOSE_OBJECT_STORAGE_PROVIDER`, `COMPOSE_OBJECT_STORAGE_ROOT`, `COMPOSE_EXPORT_STORAGE_DIR`
- `COMPOSE_AGENT_RUNTIME_BASE_URL`, `COMPOSE_CONVERSATION_ORCHESTRATOR_BASE_URL`
- `*_PORT` (`API_GATEWAY_PORT`, `CONVERSATION_ORCHESTRATOR_PORT`, ...)

### 3) 상태 확인

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8001/readyz
```

### 4) 중지/정리

```bash
docker compose down
```

데이터 볼륨까지 삭제하려면:

```bash
docker compose down -v
```

### 5) 마이그레이션 수동 실행(필요 시)

```bash
docker compose run --rm migrate python scripts/migrate.py status
docker compose run --rm migrate python scripts/migrate.py up
```

## 개발 품질 체크

- lint: `make lint`
- test: `make test`
- CI와 동일한 로컬 게이트: `make ci`

## 주요 환경 변수

- 공통
  - `SERVICE_HOST` (기본 `127.0.0.1`)
  - `DATABASE_URL`
- DB 풀
  - `DB_POOL_ENABLED`
  - `DB_POOL_REQUIRE`
  - `DB_POOL_MIN_SIZE`
  - `DB_POOL_MAX_SIZE`
  - `DB_POOL_TIMEOUT_SECONDS`
- 저장소 경로
  - `OBJECT_STORAGE_ROOT`
  - `EXPORT_STORAGE_DIR`
- LLM 런타임
  - `AGENT_RUNTIME_PROVIDER` (`stub|openai|anthropic|google`)
  - `AGENT_AI_1_PROVIDER`, `AGENT_AI_2_PROVIDER`, `AGENT_DEFAULT_PROVIDER`
    (`stub|openai|anthropic|google`)
  - `AGENT_RUNTIME_TIMEOUT_SECONDS`
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL`
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_VERSION`
  - `GOOGLE_API_KEY`, `GOOGLE_BASE_URL`
- 보안/인증
  - `INTERNAL_API_TOKEN` (설정 시 non-health 내부 API는 `x-internal-api-token` 필요)
  - `x-internal-role`, `x-auth-tenant-id`, `x-auth-workspace-id` 헤더로 역할/스코프 제어

세부값은 `.env.example`를 참고하세요.

`/internal/agents/run`의 `requested_model`은 `provider:model` 형식도 지원합니다.
예: `openai:gpt-4.1-mini`, `anthropic:claude-3-7-sonnet-latest`,
`google:gemini-2.0-flash`.

## API 탐색

서비스별 FastAPI docs:

- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- [http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs)
- [http://127.0.0.1:8003/docs](http://127.0.0.1:8003/docs)
- [http://127.0.0.1:8004/docs](http://127.0.0.1:8004/docs)
- [http://127.0.0.1:8005/docs](http://127.0.0.1:8005/docs)
- [http://127.0.0.1:8006/docs](http://127.0.0.1:8006/docs)

## 배포 참고

배포 관련 상세 문서는 [docs/BUILD_AND_DEPLOY.md](docs/BUILD_AND_DEPLOY.md)를 참고하세요.

## 상세 문서 (Wiki)

프로젝트 상세 설명은 [`wiki/README.md`](wiki/README.md)부터 확인하세요.
