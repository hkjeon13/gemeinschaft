# Local Development and Operations

## 로컬 실행 절차

1. 환경 파일 준비

```bash
cp .env.example .env
```

2. 가상환경/의존성 설치

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

3. DB 마이그레이션

```bash
make db-migrate-up
make db-migrate-status
```

4. 서비스 실행

```bash
make dev
# 또는
make dev-reload
```

## 개별 서비스 실행

```bash
python -m uvicorn services.conversation_orchestrator.app:app --host 127.0.0.1 --port 8001
python -m uvicorn services.data_ingestion.app:app --host 127.0.0.1 --port 8003
python -m uvicorn services.scheduler.app:app --host 127.0.0.1 --port 8006
```

## Docker Compose 실행

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

중요:

- `migrate` 컨테이너가 선행 실행되어 스키마를 적용합니다.
- 앱 컨테이너는 `postgres` healthy + `migrate` 성공 이후 시작됩니다.

수동 마이그레이션 명령:

```bash
docker compose run --rm migrate python scripts/migrate.py status
docker compose run --rm migrate python scripts/migrate.py up
```

## 테스트/품질

```bash
make lint
make test
make ci
```

## 운영 체크리스트

- DB extension 준비
  - `pgcrypto`
  - `vector` (pgvector)
- `DATABASE_URL` 정상 연결 확인
- `INTERNAL_API_TOKEN` 설정 여부 확인
- storage path 준비
  - `OBJECT_STORAGE_ROOT`
  - `EXPORT_STORAGE_DIR`

## 문제 해결 팁

- 마이그레이션 실패:
  - DB 권한(특히 extension create 권한) 확인
  - `make db-migrate-status`로 적용 상태 확인
- 401 오류:
  - `x-internal-api-token`과 `INTERNAL_API_TOKEN` 일치 확인
- 403 오류:
  - role/scope 헤더와 리소스 tenant/workspace 일치 확인
- agent runtime 연동 실패:
  - `AGENT_RUNTIME_BASE_URL`, `CONVERSATION_ORCHESTRATOR_BASE_URL` 확인

## 로그/관측

- 공통 미들웨어에서 request latency와 status를 로깅
- `x-request-id`를 기준으로 서비스 로그 연계 추적 가능
