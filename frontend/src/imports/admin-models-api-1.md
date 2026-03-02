 아래가 현재 기준 명세입니다.

  Base

  - 기본 라우트: POST/PATCH /api/admin/models...
  - 내부 prefix는 ADMIN_ROUTE 환경변수로 변경 가능(기본 /admin).

  1) 모델 등록

  - POST /api/admin/models
  - 권한: admin:model:create
  - Request Body (application/json)

  {
    "model_id": "optional string",
    "provider": "openai",
    "openai_api": "chat.completions",
    "model": "gpt-4o-mini",
    "display_name": "optional string",
    "description": "optional string",
    "parameters": {},
    "client_options": {},
    "chat_create_options": {},
    "responses_create_options": {},
    "api_key": "optional string",
    "api_keys": ["optional", "string", "array"],
    "webhook_secret": "optional string",
    "is_active": true,
    "is_default": false
  }

  - api_key / api_keys 규칙
  - 둘 중 하나만 사용 가능. 둘 다 보내면 400.
  - api_keys는 비어있지 않은 문자열 배열이어야 함.
  - 중복 key는 저장 전에 dedupe 처리됨.
  - Response: 201 Created + AdminChatModelSchema
  - 응답에는 실제 key 값이 아니라 has_api_key: boolean만 내려감.

  2) 모델 수정

  - PATCH /api/admin/models/{model_id}
  - 권한: admin:model:update
  - Request Body (application/json, 전부 optional)

  {
    "provider": "optional string",
    "openai_api": "optional string",
    "model": "optional string",
    "display_name": "optional string",
    "description": "optional string",
    "parameters": {},
    "client_options": {},
    "chat_create_options": {},
    "responses_create_options": {},
    "api_key": "optional string",
    "api_keys": ["optional", "string", "array"],
    "clear_api_key": true,
    "webhook_secret": "optional string",
    "clear_webhook_secret": true,
    "is_active": true,
    "is_default": false
  }

  - api_key / api_keys / clear_api_key 동작
  - api_keys 전달 시 기존 키셋 전체 교체.
  - api_key 전달 시 단일 키로 전체 교체.
  - clear_api_key: true만 보내면 키 전체 삭제.
  - clear_api_key: true와 api_key/api_keys를 함께 보내면 최종적으로 새 key 값으로 설정됨.
  - 아무 필드도 안 보내면 400 (At least one field must be provided.).
  - Response: 200 OK + AdminChatModelSchema

  공통 에러

  - 400 입력/검증 오류 (Use either api_key or api_keys, not both., api_keys must be a list of non-empty strings. 등)
  - 404 (수정 시 모델 없음)
  - 409 (등록 시 model_id 중복)
  - 422 Pydantic 타입 검증 오류

  Response Schema (AdminChatModelSchema)

  {
    "model_id": "string",
    "provider": "string",
    "openai_api": "string",
    "model": "string",
    "display_name": "string",
    "description": "string",
    "parameters": {},
    "client_options": {},
    "chat_create_options": {},
    "responses_create_options": {},
    "has_api_key": true,
    "has_webhook_secret": false,
    "is_active": true,
    "is_default": false,
    "created_at": "ISO-8601 string",
    "updated_at": "ISO-8601 string"
  }
