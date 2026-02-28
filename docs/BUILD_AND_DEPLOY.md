# Build & Deploy Guide

`gemeinschaft`는 Python 3.12 + FastAPI 기반의 멀티 서비스 애플리케이션입니다.
현재 저장소에는 Docker/Kubernetes 매니페스트가 없으므로, 이 문서는 "소스 배포 + 프로세스 매니저(systemd 등)" 기준으로 작성합니다.

## 1. 사전 준비

필수:

- Python `3.12+`
- PostgreSQL (pgvector extension 사용 가능해야 함)
- `git`

권장:

- Linux 서버에서 `systemd`로 서비스 관리
- 배포 계정 전용 가상환경 사용

## 2. 빌드(의존성 설치 + 아티팩트 생성)

### 2.1 소스 체크아웃

```bash
git clone <repo-url> gemeinschaft
cd gemeinschaft
```

### 2.2 가상환경 생성 및 의존성 설치

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2.3 (선택) wheel/sdist 아티팩트 생성

CI/CD에서 "빌드 결과물"을 보관하려면 아래를 추가합니다.

```bash
python -m pip install build
python -m build
```

생성물은 `dist/` 아래에 생성됩니다.

## 3. 배포 환경 변수 설정

```bash
cp .env.example .env
```

운영 배포 시 최소 수정 권장값:

- `SERVICE_HOST=0.0.0.0`
- `DATABASE_URL=postgresql://...`
- `INTERNAL_API_TOKEN=<강한 랜덤 값>`
- `DB_POOL_ENABLED=true`
- `DB_POOL_REQUIRE=true`
- `OBJECT_STORAGE_ROOT=/var/lib/gemeinschaft/object_storage` (절대경로 권장)
- `EXPORT_STORAGE_DIR=/var/lib/gemeinschaft/exports` (절대경로 권장)

연동 필요 시:

- `AGENT_RUNTIME_BASE_URL=http://<agent-runtime-host>:8002`
- `CONVERSATION_ORCHESTRATOR_BASE_URL=http://<orchestrator-host>:8001`
- `ORCHESTRATOR_INTERNAL_API_TOKEN=<토큰>` (비우면 `INTERNAL_API_TOKEN` 재사용)

## 4. DB 마이그레이션

`db/migrations`의 SQL을 적용합니다.

```bash
make db-migrate-up
```

상태 확인:

```bash
make db-migrate-status
```

주의:

- 마이그레이션 `0005`에서 `CREATE EXTENSION IF NOT EXISTS vector;`를 수행합니다.
- DB 권한 정책상 extension 생성이 제한되어 있으면 DBA가 사전 설치해야 합니다.

## 5. 서비스 실행 방식

서비스는 총 7개입니다.

- `api_gateway` (`8000`)
- `conversation_orchestrator` (`8001`)
- `agent_runtime` (`8002`)
- `data_ingestion` (`8003`)
- `topic_pipeline` (`8004`)
- `export_service` (`8005`)
- `scheduler` (`8006`)

로컬 통합 실행(개발/검증):

```bash
make dev
```

운영 배포(권장): 서비스별 개별 프로세스로 실행

```bash
python -m uvicorn services.api_gateway.app:app --host 0.0.0.0 --port 8000
python -m uvicorn services.conversation_orchestrator.app:app --host 0.0.0.0 --port 8001
python -m uvicorn services.agent_runtime.app:app --host 0.0.0.0 --port 8002
python -m uvicorn services.data_ingestion.app:app --host 0.0.0.0 --port 8003
python -m uvicorn services.topic_pipeline.app:app --host 0.0.0.0 --port 8004
python -m uvicorn services.export_service.app:app --host 0.0.0.0 --port 8005
python -m uvicorn services.scheduler.app:app --host 0.0.0.0 --port 8006
```

## 6. systemd 예시

아래는 `api_gateway` 예시입니다. 나머지 서비스도 `ExecStart` 모듈/포트만 바꿔 동일하게 생성합니다.

```ini
[Unit]
Description=gemeinschaft api_gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/gemeinschaft/current
EnvironmentFile=/opt/gemeinschaft/shared/.env
ExecStart=/opt/gemeinschaft/current/.venv/bin/python -m uvicorn services.api_gateway.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
User=gemeinschaft
Group=gemeinschaft

[Install]
WantedBy=multi-user.target
```

반영:

```bash
sudo systemctl daemon-reload
sudo systemctl enable gemeinschaft-api-gateway
sudo systemctl restart gemeinschaft-api-gateway
```

## 7. 권장 배포 순서

1. 새 릴리스 코드 배치
2. 가상환경/의존성 업데이트
3. `make db-migrate-up` 실행
4. 서비스 순차 재시작(또는 롤링 재시작)
5. `/healthz`, `/readyz` 확인
6. 스모크 테스트(핵심 API 1~2개) 실행

## 8. 배포 후 점검

헬스체크 예시:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8001/readyz
curl -fsS http://127.0.0.1:8006/healthz
```

품질 게이트(권장):

```bash
make ci
```

## 9. 롤백 가이드(최소)

1. 이전 릴리스 코드/venv로 심볼릭 링크 복구
2. 서비스 재시작
3. DB 롤백이 꼭 필요할 때만 `make db-migrate-down` 또는 `python scripts/migrate.py down --steps <N>`를 신중 적용

DB down migration은 데이터 영향이 있을 수 있으므로 운영에서는 사전 검증 후 수행합니다.
