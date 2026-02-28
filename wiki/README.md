# Project Wiki

`gemeinschaft` 프로젝트를 코드 기준으로 자세히 설명하는 문서 모음입니다.

## 읽기 순서 (권장)

1. [01_system_overview.md](01_system_overview.md)
2. [02_service_catalog.md](02_service_catalog.md)
3. [03_domain_and_data_model.md](03_domain_and_data_model.md)
4. [04_runtime_flows.md](04_runtime_flows.md)
5. [05_api_and_auth.md](05_api_and_auth.md)
6. [06_local_dev_and_operations.md](06_local_dev_and_operations.md)
7. [07_extension_guide.md](07_extension_guide.md)

## 문서 원칙

- 이 위키는 현재 코드베이스(`services/`, `db/migrations/`)를 기준으로 작성되었습니다.
- 외부 제품/인프라 가정은 최소화하고, 저장소 내 구현 사실 중심으로 기술합니다.
- API 상세 스키마는 각 서비스의 `/docs`(FastAPI OpenAPI)를 함께 참고하세요.
