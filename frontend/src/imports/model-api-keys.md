
  Base

  - POST /api/admin/models
  - PATCH /api/admin/models/{model_id}

  1) 모델 등록 (POST)

  - 키 관련 필드:

  {
    "api_key": "optional string",
    "api_keys": ["optional", "string", "array"]
  }

  - 규칙:
      - api_key와 api_keys 동시 사용 불가 (400)
      - api_keys는 비어있지 않은 문자열 배열이어야 함
      - 중복 값은 저장 시 dedupe

  2) 모델 수정 (PATCH)

  - 새로 추가된 필드:

  {
    "append_api_keys": ["string", "array"]
  }

  - 키 관련 전체 필드:

  {
    "api_key": "optional string",
    "api_keys": ["optional", "string", "array"],
    "append_api_keys": ["optional", "string", "array"],
    "clear_api_key": true
  }

  - 처리 순서(중요):

  1. clear_api_key=true면 기존 키 전부 비움
  2. api_key 또는 api_keys가 오면 키셋 교체
  3. append_api_keys가 오면 현재 키셋 뒤에 추가(append), 중복은 자동 제거

  - 검증 규칙:
      - api_key + api_keys 동시 사용 불가 (400)
      - append_api_keys는 비어있지 않은 문자열 배열이어야 함 (400)
      - append_api_keys만 보내도 유효한 PATCH로 처리됨

  예시

  - 기존에 1개 추가:

  {
    "append_api_keys": ["sk-new-1"]
  }

  - 교체 후 추가:

  {
    "api_keys": ["sk-base-1"],
    "append_api_keys": ["sk-extra-1", "sk-extra-2"]
  }

  - 모두 지우고 새 키들 추가:

  {
    "clear_api_key": true,
    "append_api_keys": ["sk-fresh-1", "sk-fresh-2"]
  }

  응답

  - 기존과 동일 (AdminChatModelSchema)
  - 실제 키 값은 반환하지 않고 has_api_key: boolean만 반환.
