---
name: sync-ops
description: HubSpot/Notion のデータ同期を安全に実行し、結果を検証する
argument-hint: "[hubspot|notion|all]"
user-invocable: true
allowed-tools: "Read, Grep, Bash(make *), Bash(python *)"
---

# Sync Operations Agent

同期対象: $ARGUMENTS（未指定なら `all`）

## 実行手順

### 1. プリフライトチェック
- Docker サービス（PostgreSQL, Redis）が起動しているか確認: `make docker-up` の状態
- `.env` に必要な API キーが設定されているか確認（値は表示しない）
  - HubSpot: `HUBSPOT_API_KEY`, `HUBSPOT_PORTAL_ID`
  - Notion: `NOTION_API_KEY`, `NOTION_SOP_DATABASE_ID`
- Kill switch が有効でないか確認

### 2. 同期実行
- HubSpot: `make sync-hubspot`
  - コンタクト数、新規/更新件数をログから取得
  - エラーがあれば即座に報告
- Notion: `make sync-sops`
  - SOP ページ数、カテゴリ別件数をログから取得
  - エラーがあれば即座に報告

### 3. 事後検証
- DB にレコードが正しく反映されたか確認
- Redis キャッシュが更新されたか確認
- エラーログがないか確認

### 4. レポート出力

```
## Sync Report

### HubSpot Contacts
- Total synced: N
- New: N / Updated: N / Skipped: N
- Errors: N

### Notion SOPs
- Total synced: N
- Categories: [list]
- Errors: N

### Status: SUCCESS / PARTIAL / FAILED
```
