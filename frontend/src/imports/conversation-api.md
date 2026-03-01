• 기본

  - Base: /api/conversation
  - 인증: 기존 로그인 세션/토큰 방식 그대로
  - conversation_id: conv-* 형태 문자열

  메시지/역할 규칙 (중요)

  - 서버 저장 기준 핵심 식별자는 model_id
  - 응답 messages[].role은 파생값:
      - message.model_id == conversation.user_id 이면 user
      - 그 외는 assistant
  - 모델 입력 직전 히스토리 변환:
      - message.model_id == 현재 선택된 selected_model_id 이면 assistant
      - 다르면 user

  메시지 구조

  {
    "message_id": "uuid",
    "role": "user|assistant",
    "message": "text preview",
    "content": [
      {"type":"input_text","text":"..."},
      {"type":"input_image","image_url":"..."}
    ],
    "created_at": "ISO8601",
    "model_id": "string",
    "model_name": "string|null",
    "model_display_name": "string|null",
    "provider": "openai|null"
  }

  1) 대화 목록 조회

  - GET /api/conversation/list
  - 응답: [{ conversation_id, title, message_count, updated_at, has_unread }]

  2) 대화 상세 조회

  - GET /api/conversation/{conversation_id}
  - 응답: { conversation_id, tenant_id, user_id, title, messages, updated_at }
  - 조회 시 읽음 처리됨

  3) 사용자 메시지 전송 + 답변 생성

  - POST /api/conversation/{conversation_id}?stream=false|true
  - Body(model_id와 model_ids 동시 사용 불가):

  {
    "message": "안녕",
    "messages": [
      {"role":"user","content":[{"type":"input_text","text":"안녕"}]}
    ],
    "model_id": "optional-single-model",
    "model_ids": ["optional", "candidate", "models"]
  }

  - 모델 선택 우선순위:
      1. model_id 고정
      2. model_ids 중 랜덤 1개
      3. 대화방 등록 모델 리스트 중 랜덤 1개
  - stream=false: 최종 ConversationDetail 반환
  - stream=true: text/event-stream
      - event: delta → {"text":"..."}
      - event: done → {"conversation_id","model_id","model_name","model_display_name","provider"}
      - event: error → {"detail":"..."}

  4) 연속 대화(입력 없이 이어서 답변)

  - POST /api/conversation/{conversation_id}/continue?stream=false|true
  - Body:

  {
    "model_id": "optional-single-model",
    "model_ids": ["optional", "candidate", "models"],
    "min_interval_seconds": 1,
    "max_interval_seconds": 10,
    "max_turns": 20
  }

  - 동작:
      - 현재 대화 마지막 맥락으로 바로 답변 생성
      - 답변 전 랜덤 지연 적용: uniform(min, max)
      - min/max 미지정 시 환경 기본값 사용(기본 1~10초)
  - max_turns:
      - assistant 턴 수가 이미 >= max_turns면 생성 안 함
      - 응답: 409 Conflict, {"detail":"max_turns reached (N)."}

  5) 대화방 모델 리스트 조회/관리

  - GET /api/conversation/{conversation_id}/models
  - POST /api/conversation/{conversation_id}/models body: {"model_id":"..."}
  - DELETE /api/conversation/{conversation_id}/models/{model_id}

  6) 사용자 기본 모델

  - GET /api/conversation/model/default
  - PUT /api/conversation/model/default body: {"model_id":"..."}
  - DELETE /api/conversation/model/default
  - 선택 가능한 모델 목록: GET /api/conversation/model/list

  7) 제목/삭제

  - 제목 수정: PATCH /api/conversation/{conversation_id}/title body: {"title":"..."}
  - 숨김 삭제: DELETE /api/conversation/{conversation_id}

  프론트 권장 구현 흐름 (연속 대화)

  1. 연속 대화 ON 시 /continue 호출 루프 시작
  2. 각 호출에 model_ids, max_turns, 인터벌 범위 전달
  3. 409 받으면 루프 종료(최대 턴 도달)
  4. stream=true면 delta 실시간 렌더, done에서 목록/상세 갱신