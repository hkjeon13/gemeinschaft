 Base URL

  - 프론트 프록시 기준: /api/admin
  - 앱 직접 호출 시: /admin

  인증/권한

  - 공통: 로그인된 JWT 필요, role=admin 필요
  - 기본 설정에서 DPoP 필요 (AUTH_REQUIRE_DPOP=true)
  - POST/PATCH/DELETE는 Origin 헤더 필요
  - 쿠키 인증을 쓰는 경우 POST/PATCH/DELETE에 x-csrf-token도 필요

  ———

  ### 1. 모델 목록 조회

  GET /api/admin/models

  - Request body: 없음
  - Response 200

  [
    {
      "model_id": "default",
      "provider": "openai",
      "client_type": "openai",
      "model": "gpt-4o-mini",
      "display_name": "gpt-4o-mini",
      "description": "Default chat model.",
      "parameters": {},
      "has_api_key": false,
      "is_active": true,
      "is_default": true,
      "created_at": "2026-02-28T12:00:00Z",
      "updated_at": "2026-02-28T12:00:00Z"
    }
  ]

  ———

  ### 2. 모델 등록

  POST /api/admin/models

  - Request body

  {
    "model_id": "gpt4o-mini-prod",
    "provider": "openai",
    "client_type": "openai",
    "model": "gpt-4o-mini",
    "display_name": "GPT-4o Mini Prod",
    "description": "production model",
    "parameters": {
      "temperature": 0.7,
      "max_tokens": 1024
    },
    "api_key": "sk-...",
    "is_active": true,
    "is_default": false
  }

  - 필수: model_id, model
  - 기본값:
      - provider="openai"
      - client_type="openai"
      - description=""
      - parameters={}
      - is_active=true
      - is_default=false
  - Response 201: 목록 아이템과 동일 스키마 (api_key는 절대 반환 안 됨, has_api_key만 반환)

  ———

  ### 3. 모델 수정

  PATCH /api/admin/models/{model_id}

  - Request body (모두 optional, 단 최소 1개 필드 필요)

  {
    "display_name": "GPT-4o Mini A",
    "parameters": {"temperature": 0.3},
    "api_key": "sk-new...",
    "clear_api_key": false,
    "is_active": true,
    "is_default": true
  }

  - 가능한 필드:
      - provider, client_type, model, display_name, description
      - parameters (JSON object)
      - api_key (새 키 저장, 암호화 저장)
      - clear_api_key (true면 기존 키 제거)
      - is_active, is_default
  - Response 200: 목록 아이템과 동일 스키마

  ———

  ### 4. 모델 삭제

  DELETE /api/admin/models/{model_id}

  - Response 204 (body 없음)
  - 마지막 남은 모델은 삭제 불가 (400)

  ———

  주요 에러 코드

  - 401: JWT/DPoP 문제
  - 403: admin 권한 없음, Origin/CSRF 실패
  - 404: 모델 없음
  - 409: model_id 중복(등록 시)
  - 422: body 타입/필드 검증 실패
  - 500: api_key 저장 시 MODEL_SECRET_ENCRYPTION_KEY 미설정/오류 등 서버 설정 문제

  원하면 이걸 바로 프론트용 TypeScript 타입 + API client 함수 형태로 뽑아드릴게요.