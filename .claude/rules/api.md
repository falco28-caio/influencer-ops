---
paths:
  - "src/api/**/*.py"
---

# API Rules

- All routes are in `src/api/routes.py` under prefix `/api/v1`
- Request/response models go in `src/api/schemas.py` as Pydantic v2 BaseModel
- ORM response models must set `class Config: from_attributes = True`
- Use dependency injection for DB sessions and services
- Return proper HTTP status codes (201 for creation, 404 for not found, etc.)
- Enum values in schemas must match the corresponding database/service enums
