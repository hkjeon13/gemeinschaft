• 아래는 현재 변경 반영 기준 API 명세입니다.
  (목록 엔드포인트: /api/conversation/list 적용됨)

  기준 코드:

  - conversation.py
  - auth.py
  - conversation schema
  - auth schema

  공통

  - 외부 호출 경로는 /api/*
  - 보호 API는 DPoP 헤더 필요
  - POST는 Origin 검사 + CSRF(x-csrf-token) 필요
  - 쿠키 기반이면 credentials: include 권장

  인증/보안 로직:

  - require_jwt:1192
  - origin/csrf/dpop:92

  ———

  1) 로그인

  - POST /api/auth/login
  - Body

  { "username": "psyche", "password": "..." }

  - 200 Response

  {
    "token_type": "bearer",
    "access_expires_in": 900,
    "refresh_expires_in": 1209600,
    "csrf_token": "..."
  }

  - 쿠키(access_token, refresh_token, csrf_token)도 Set-Cookie로 내려감

  2) 토큰 갱신

  - POST /api/auth/refresh
  - Body (선택)

  { "refresh_token": "..." }

  - 없으면 쿠키의 refresh token 사용
  - 200 Response: login과 동일 스키마

  3) 내 세션 조회

  - GET /api/auth/me
  - 200 Response

  {
    "sub": "psyche",
    "role": "admin",
    "tenant": "default",
    "scope": "conversation:read conversation:write",
    "iss": "...",
    "aud": "...",
    "typ": "...",
    "exp": 1234567890
  }

  ———

  4) 대화 목록 (변경됨)

  - GET /api/conversation/list
  - 권한: conversation:read
  - 200 Response

  [
    {
      "conversation_id": "conv-1",
      "message_count": 2,
      "updated_at": "2026-02-28T10:00:00Z"
    }
  ]

  5) 대화 상세

  - GET /api/conversation/{conversation_id}
  - 권한: conversation:read
  - 200 Response

  {
    "conversation_id": "conv-1",
    "tenant_id": "default",
    "user_id": "psyche",
    "messages": [
      {
        "message_id": "uuidhex",
        "message": "hello",
        "created_at": "2026-02-28T10:00:00Z"
      }
    ],
    "updated_at": "2026-02-28T10:00:00Z"
  }

  - 404: {"detail":"Conversation not found."}

  6) 메시지 추가

  - POST /api/conversation/{conversation_id}
  - 권한: conversation:write
  - Body

  { "message": "..." }

  - message 길이: 1~4000
  - 200 Response: 대화 상세와 동일 (append 후 전체 반환)
