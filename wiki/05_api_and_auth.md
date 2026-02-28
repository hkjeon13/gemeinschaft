# API and Authorization

## 내부 인증 모델

공통 미들웨어(`services/shared/app_factory.py`)가 non-health 엔드포인트에 적용됩니다.

- `INTERNAL_API_TOKEN`이 설정되어 있으면
  - 요청 헤더 `x-internal-api-token` 필수
  - 불일치/누락 시 `401`
- role 기본값: `system` (`x-internal-role`)
- scope 헤더(선택):
  - `x-auth-tenant-id`
  - `x-auth-workspace-id`

## Role/Scope enforcement

서비스 핸들러는 필요 시 아래를 사용합니다.

- `enforce_role(...)`
- `enforce_scope(...)`

결과:

- role 불일치: `403` + allowed_roles 정보
- tenant/workspace mismatch: `403`

## request tracing

- 모든 요청에 `x-request-id`가 응답 헤더로 포함됩니다.
- 요청 시 `x-request-id`를 직접 주면 그대로 전달됩니다.

## 대표 에러 시나리오

- `401`: 내부 토큰 누락/불일치
- `403`: role/scope 위반
- `404`: conversation/template/source/job 없음
- `409`: 이벤트 시퀀스 충돌(append 경로)
- `422`: Pydantic request validation 실패

## 호출 예시

```bash
curl -X POST http://127.0.0.1:8001/internal/events/append \
  -H 'content-type: application/json' \
  -H 'x-internal-api-token: <token>' \
  -H 'x-internal-role: system' \
  -d '{
    "conversation_id": "00000000-0000-0000-0000-000000000001",
    "event_type": "custom.event",
    "expected_seq_no": 2,
    "payload": {"k": "v"}
  }'
```

```bash
curl -X GET 'http://127.0.0.1:8003/internal/sources/page?limit=20' \
  -H 'x-internal-api-token: <token>' \
  -H 'x-internal-role: viewer' \
  -H 'x-auth-tenant-id: <tenant-uuid>' \
  -H 'x-auth-workspace-id: <workspace-uuid>'
```

## 운영 권장

- 개발 환경 제외하고 `INTERNAL_API_TOKEN`을 항상 설정
- 서비스 간 호출에도 토큰/role/principal을 명시
- 멀티 테넌트 운영에서는 scope 헤더를 강제하는 게 안전
