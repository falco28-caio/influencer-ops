# InfluencerOps Agent

Influencer communications AI agent — email triage, draft generation, workflow automation.

## Commands

```bash
make dev              # uvicorn --reload on :8000
make test             # pytest with coverage
make lint             # ruff check + mypy strict
make format           # black + ruff --fix
make migrate          # alembic upgrade head
make docker-up        # PostgreSQL + Redis
make worker           # Celery worker
make beat             # Celery beat scheduler
```

Single test: `pytest tests/test_triage.py -v`

## Code Style — Hard Rules

- `from __future__ import annotations` at the top of **every** `.py` file
- Type annotations on **all** function signatures (mypy strict)
- black line-length=100, ruff rules: E/F/I/N/W/UP
- Import order: stdlib → third-party → `src.*` (ruff `I` enforces this)
- Logging: `structlog` via `get_logger()` with **structured kwargs only**

```python
# GOOD
self.logger.info("Draft generated", task_id=str(task.id), confidence=0.92)

# BAD — never use f-strings in log calls
self.logger.info(f"Draft generated for task {task.id}")
```

## Key Patterns

### Services — Stateless, always get logger in `__init__`
```python
class MyService:
    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
```

### Models — SQLAlchemy 2.0 style only
```python
# GOOD
name: Mapped[str] = mapped_column(String(255), nullable=False)

# BAD — legacy Column() style is forbidden
name = Column(String(255), nullable=False)
```

### Schemas — Pydantic v2 with ORM mode
```python
class FooResponse(BaseModel):
    class Config:
        from_attributes = True  # not orm_mode
```

### LLM Calls — Always follow this pattern
```python
# 1. Sanitize user content before embedding in prompts
sanitized = GuardrailService().sanitize_for_prompt(user_content)

# 2. Use settings for model config
response = client.messages.create(
    model=settings.llm_model,  # never hardcode model names
    max_tokens=settings.llm_max_tokens,
    temperature=0.3,  # low for classification, 0.7 for generation
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": prompt}],
)

# 3. Parse JSON — always handle ```json blocks
response_text = response.content[0].text
result = self._parse_response(response_text)  # strips markdown fences
```

### Config — All via settings singleton
```python
from src.core.config import settings
# Secrets: settings.anthropic_api_key.get_secret_value()
# Never hardcode API keys, URLs, or thresholds
```

## Workflow State Machine

```
NEW → TRIAGING → DRAFTING → REVIEWING → APPROVED → SENDING → SENT → WAITING_REPLY → CLOSED
                    ↑          ↓    ↑                                    ↓
                    |       EDITING──┘                           FOLLOW_UP_NEEDED
                    |          ↓
                    └─── FAILED (retry ≤3x) → ESCALATED
```

TRIAGING filters `spam`/`out_of_scope` → CLOSED. Guards use `context.get("intent")`.

## Security — Non-Negotiable

1. **Guardrail before outbound**: `GuardrailService.check_content()` must run before sending ANY email or Slack message
2. **Sanitize before LLM**: `sanitize_for_prompt()` on all user-provided content going into prompts
3. **Kill switch**: Check `check_kill_switch()` before any auto-send in workers
4. **Secrets**: All API keys are `SecretStr` in Settings. Access via `.get_secret_value()`
5. **Never commit**: `.env`, `*_token.json`, `*_credentials.json`

## Testing

- pytest-asyncio in `auto` mode — just write `async def test_*`
- DB: in-memory SQLite via `aiosqlite` (fixtures in `conftest.py`)
- **Always mock external services** — never hit real Gmail/Slack/HubSpot/Anthropic in tests
- File naming: `tests/test_<module>.py`

```python
# Mock pattern for Anthropic client
from unittest.mock import AsyncMock, MagicMock, patch

mock_response = MagicMock()
mock_response.content = [MagicMock(text='{"intent": "collab_inquiry", "confidence": 0.95}')]

with patch.object(agent._client.messages, "create", return_value=mock_response):
    result = await agent.analyze(email_body="...", email_subject="...", sender="...")
```

## Anti-Patterns — Do NOT

- **Don't use `Column()`** — SQLAlchemy 2.0 `mapped_column()` only
- **Don't call real APIs in tests** — always mock
- **Don't hardcode model names** — use `settings.llm_model`
- **Don't use `orm_mode`** — it's `from_attributes` in Pydantic v2
- **Don't log PII** — use `mask_for_logging()` before logging email content
- **Don't skip guardrails** — even for "internal" or "test" emails
- **Don't use sync DB/Redis calls** — everything is async in this codebase
- **Don't create services with state** — services are stateless; state goes to Redis or DB

## Agent Team — Autonomous Operations

このプロジェクトでは Hooks + Skills + Rules による Agent Team 体制が構築されている。

### Hooks（自動ゲート — 人間の介入なしで実行）
- **PostToolUse (Edit/Write)**: Python ファイル編集後に自動で `black` + `ruff --fix` を実行
- **PreToolUse (git commit)**: コミット前に `ruff check` + `pytest` を実行。失敗したらコミットをブロック

### Skills（専門エージェント — `/skill名` で起動）
- `/review-pr [PR番号]` — セキュリティ・品質・ドメイン知識の5観点でPRを徹底レビュー
- `/security-audit [対象]` — ガードレール漏れ・PII漏洩・シークレット漏洩・injection脆弱性をスキャン
- `/sync-ops [hubspot|notion|all]` — データ同期をプリフライトチェック付きで安全に実行
- `/health-check` — 全サービス（DB, Redis, API, Celery, 外部API）の接続状態を診断

### Rules（パスベース自動ロード — 対象ファイル操作時に発動）
- `database.md` → models/alembic 操作時
- `api.md` → API routes/schemas 操作時
- `adapters.md` → 外部サービスアダプター操作時
- `security.md` → ガードレール/エージェント/ドラフト操作時
- `workers.md` → Celery タスク操作時
- `rag.md` → RAG サービス操作時
- `testing.md` → テストファイル操作時

### 自律行動ガイドライン
1. **コード変更後**: `make test` を実行し、失敗したら修正してからコミット
2. **新機能追加時**: 対応するテストファイル `tests/test_<module>.py` も作成
3. **セキュリティ関連の変更時**: `/security-audit` で自己検証
4. **PR作成前**: `/review-pr` で自己レビューし、指摘事項を修正

## Git Conventions

- Commit format: `<type>: <description>` (e.g., `feat: add campaign pause endpoint`)
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- One logical change per commit
