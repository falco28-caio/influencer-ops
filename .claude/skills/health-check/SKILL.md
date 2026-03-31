---
name: health-check
description: 全サービスの接続状態とシステムヘルスを診断する
user-invocable: true
allowed-tools: "Read, Grep, Bash(curl *), Bash(docker *), Bash(redis-cli *), Bash(make *)"
---

# Health Check Agent

全サービスの接続状態を診断し、問題があれば修復手順を提示する。

## チェック項目

### 1. Infrastructure
- **PostgreSQL**: `docker compose ps db` で起動確認 + `pg_isready` で接続確認
- **Redis**: `docker compose ps redis` で起動確認 + `redis-cli ping` で接続確認
- **ChromaDB**: `docker compose ps chromadb` で起動確認（オプショナル）

### 2. Application
- **API Server**: `curl -s http://localhost:8000/api/v1/health` でヘルスチェック
- **Celery Worker**: `celery -A src.workers.celery_app inspect ping` で疎通確認
- **Celery Beat**: プロセス存在確認

### 3. External Services
- `.env` の各キーが設定されているか（値は表示しない、存在のみ確認）
  - `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
  - `GMAIL_CLIENT_ID`, `SLACK_BOT_TOKEN`
  - `HUBSPOT_API_KEY`, `NOTION_API_KEY`

### 4. Kill Switch
- Redis の `GLOBAL_STOP` キーの状態を確認

## 出力フォーマット

```
## System Health Report

| Service      | Status | Details          |
|-------------|--------|------------------|
| PostgreSQL  | ✅/❌  | Connected / Error |
| Redis       | ✅/❌  | Connected / Error |
| API Server  | ✅/❌  | Healthy / Down    |
| Celery      | ✅/❌  | N workers active  |
| Kill Switch | 🟢/🔴 | OFF / ON          |

### Issues Found
- [issue description + remediation steps]

### Overall: HEALTHY / DEGRADED / DOWN
```
