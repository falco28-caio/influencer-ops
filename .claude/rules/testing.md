---
paths:
  - "tests/**/*.py"
---

# Testing Gotchas

- `asyncio_mode = "auto"` が `pyproject.toml` で設定済み — テスト関数に `@pytest.mark.asyncio` は不要、`async def test_*` だけで動く
- DB フィクスチャ (`db_session`) は `tests/conftest.py` で定義 — インメモリ SQLite + aiosqlite を使用。PostgreSQL 固有機能（JSONB 演算子等）はテスト不可
- Anthropic クライアントのモックパターン:
  ```python
  mock_response = MagicMock()
  mock_response.content = [MagicMock(text='{"key": "value"}')]
  with patch.object(service._client.messages, "create", return_value=mock_response):
      result = await service.method(...)
  ```
- Redis のモックには `fakeredis.aioredis` を使うか、`AsyncMock` でラップする — 実 Redis に接続しない
- 1テストファイル = 1モジュール: `tests/test_triage.py` → `src/agents/triage.py`
- フィクスチャは `conftest.py` に集約 — テストファイル内でフィクスチャを定義しない
- カバレッジ: `pytest tests/ -v --cov=src --cov-report=term-missing` で確認
