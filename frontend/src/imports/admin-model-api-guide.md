
  1) 변경 요약

  1. client_type 필드는 제거되었습니다.
  2. provider 기준으로 통일되었습니다. 현재 대화 실행은 provider="openai"만 지원합니다.
  3. 모델 설정에 openai_api가 추가되었습니다.

  - 값: "chat.completions" 또는 "responses"

  4. OpenAI 호출 파라미터를 JSON 딕셔너리로 분리 저장합니다.

  - client_options
  - chat_create_options
  - responses_create_options

  5. 시크릿 관련 필드 추가

  - 입력: api_key, webhook_secret (write-only)
  - 조회: has_api_key, has_webhook_secret만 반환

  ———

  2) Admin 모델 API (프론트에서 쓰는 핵심)
  Base: /api/admin/models

  1. GET /api/admin/models

  - 모델 목록 조회
  - 응답 아이템:

  {
    "model_id": "string",
    "provider": "openai",
    "openai_api": "chat.completions",
    "model": "gpt-4o-mini",
    "display_name": "GPT-4o Mini",
    "description": "",
    "parameters": {},
    "client_options": {},
    "chat_create_options": {},
    "responses_create_options": {},
    "has_api_key": true,
    "has_webhook_secret": false,
    "is_active": true,
    "is_default": false,
    "created_at": "2026-02-28T12:00:00Z",
    "updated_at": "2026-02-28T12:00:00Z"
  }

  2. POST /api/admin/models

  - 생성
  - 요청:

  {
    "model_id": "openai-gpt4o-mini",
    "provider": "openai",
    "openai_api": "chat.completions",
    "model": "gpt-4o-mini",
    "display_name": "GPT-4o Mini",
    "description": "default",
    "parameters": {"temperature": 0.7, "max_tokens": 1024},
    "client_options": {"project": "proj_xxx"},
    "chat_create_options": {"reasoning_effort": "medium"},
    "responses_create_options": {},
    "api_key": "sk-...",
    "webhook_secret": "whsec-...",
    "is_active": true,
    "is_default": true
  }

  3. PATCH /api/admin/models/{model_id}

  - 부분 수정
  - 최소 1개 필드 필요
  - 시크릿 삭제:
      - clear_api_key: true
      - clear_webhook_secret: true
  - 예시:

  {
    "openai_api": "responses",
    "responses_create_options": {"reasoning": {"effort": "medium"}},
    "clear_api_key": false,
    "is_active": true
  }

  4. DELETE /api/admin/models/{model_id}

  - 삭제
  - 마지막 1개 모델은 삭제 불가 (400)

  ———

  3) 필드별 프론트 처리 가이드

  1. 기본 입력 UI

  - model_id, provider, openai_api, model, display_name, description, is_active, is_default

  2. JSON 에디터 UI

  - parameters
  - client_options
  - chat_create_options
  - responses_create_options

  3. 시크릿 UI

  - 입력 전용: api_key, webhook_secret
  - 목록/상세엔 값 대신 has_api_key, has_webhook_secret로 상태만 표시
  - PATCH에서 “시크릿 삭제” 토글 제공

  4. openai_api에 따른 UX

  - chat.completions 선택 시 chat_create_options 편집 UI 강조
  - responses 선택 시 responses_create_options 편집 UI 강조
  - 두 딕셔너리는 모두 저장 가능하지만, 실행 시 선택한 API의 옵션만 사용됨

  ———

  4) 딕셔너리 검증 규칙(중요)
  서버가 등록/수정 시 바로 검증합니다.

  1. client_options 허용 키 (provider=openai일 때만 엄격 검증)

  - organization, project, websocket_base_url, base_url, timeout, max_retries, default_headers, default_query, strict_response_validation
  - 타입 제약:
      - timeout: number, >0
      - max_retries: int, >=0
      - default_headers: string -> string 맵
      - strict_response_validation: boolean

  2. chat_create_options 허용 키

  - audio, frequency_penalty, function_call, functions, logit_bias, logprobs, max_completion_tokens, max_tokens, metadata, modalities, n, parallel_tool_calls, prediction, presence_penalty, prompt_cache_key, reasoning_effort,
    response_format, safety_identifier, seed, service_tier, stop, store, stream_options, temperature, tool_choice, tools, top_logprobs, top_p, user, verbosity, web_search_options, extra_headers, extra_query, extra_body,
    timeout
  - 금지(예약) 키: messages, model, stream

  3. responses_create_options 허용 키

  - background, conversation, include, instructions, max_output_tokens, max_tool_calls, metadata, parallel_tool_calls, previous_response_id, prompt, prompt_cache_key, reasoning, safety_identifier, service_tier, store,
    stream_options, temperature, text, tool_choice, tools, top_logprobs, top_p, truncation, user, extra_headers, extra_query, extra_body, timeout
  - 금지(예약) 키: input, model, stream

  ———

  5) 에러 처리 패턴

  1. 400 비즈니스 검증 실패

  - 예: chat_create_options includes unsupported key(s): foo
  - 예: responses_create_options includes reserved key(s): input
  - 예: openai_api must be one of: chat.completions, responses.

  2. 409

  - model_id 중복

  3. 422

  - Pydantic 필드 타입/필수값 실패

  4. 401/403

  - 인증/권한 문제 (admin 권한 필요)

  ———

  6) 대화 API에서 프론트 영향

  - POST /api/conversation/{conversation_id} body에 model_id 지정 가능:

  {"message":"안녕", "model_id":"openai-gpt4o-mini"}

  - stream=true 사용 가능 (SSE)
  - 선택된 모델의 openai_api에 따라 내부적으로 chat.completions 또는 responses가 실행됩니다.