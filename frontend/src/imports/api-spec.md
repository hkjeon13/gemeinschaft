 API 명세

  1. GET /api/conversation/model/list

  - 설명: 대화에서 선택 가능한 모델 목록 조회
  - 기준: 관리자 등록 모델(레지스트리) 중 active + 현재 대화 엔진 지원 모델(provider=openai)
  - 권한: conversation:read
  - 응답 예:

  [
    {
      "model_id": "default",
      "provider": "openai",
      "openai_api": "chat.completions",
      "model": "gpt-4o-mini",
      "display_name": "GPT-4o mini",
      "description": "Default chat model",
      "is_global_default": true,
      "is_user_default": false
    }
  ]

  2. GET /api/conversation/model/default

  - 설명: 현재 사용자에게 실제 적용되는 기본 모델 조회
  - 우선순위: 사용자 기본 > 글로벌 기본 > 활성 모델 1개
  - 권한: conversation:read
  - 응답:

  {
    "model_id": "default",
    "display_name": "GPT-4o mini",
    "source": "user"
  }

  3. PUT /api/conversation/model/default

  - 설명: 사용자 기본 모델 설정
  - 권한: conversation:write
  - 요청:

  { "model_id": "default" }

  - 검증: 미등록/비활성/미지원 provider면 에러

  4. DELETE /api/conversation/model/default

  - 설명: 사용자 기본 모델 해제(글로벌 기본으로 복귀)
  - 권한: conversation:write
  - 응답: 해제 후 실제 적용 모델 반환

  대화 API 동작 변경

  - POST /api/conversation/{conversation_id}에서 model_id는 선택값입니다.
  - model_id를 생략하면 사용자 기본 모델이 자동 적용됩니다.
  - model_id를 보내면 해당 모델을 우선 사용합니다.

  예:

  {
    "messages": [
      {
        "role": "user",
        "content": [{ "type": "text", "text": "안녕?" }]
      }
    ]
  }

  저장소

  - 사용자 기본 모델은 새 테이블 user_model_preferences에 저장됩니다.
  - 백엔드 선택:
      - USER_MODEL_PREFERENCE_BACKEND=postgres|memory
      - 미설정 시 DATABASE_ENABLED=true면 postgres 기본