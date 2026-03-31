---
name: security-audit
description: コード変更に対してセキュリティ監査を実行し、脆弱性・PII漏洩・ガードレール漏れを検出する
argument-hint: "[対象パスまたはブランチ]"
user-invocable: true
allowed-tools: "Read, Grep, Glob, Agent"
---

# Security Audit Agent

対象: $ARGUMENTS（未指定なら直近の変更ファイル全体）

## 監査項目

### 1. ガードレール適用漏れ
- `Grep` で `send_message`, `send_email`, `send_approval` 等のアウトバウンド呼び出しを検索
- 各呼び出しの直前に `GuardrailService.check_content()` があるか確認
- なければ ❌ **CRITICAL** として報告

### 2. LLM プロンプトインジェクション防御
- `Grep` で `messages.create`, `client.messages` 等のLLM呼び出しを検索
- ユーザー入力が含まれるプロンプトに `sanitize_for_prompt()` が適用されているか確認
- `model=` に文字列リテラルがあれば ⚠️（`settings.llm_model` を使うべき）

### 3. シークレット漏洩
- `Grep` で以下のパターンを検索:
  - `sk-ant-`, `sk-`, `xoxb-`, `xapp-`, `secret_` などのトークンプレフィックス
  - `api_key=`, `token=`, `password=` にリテラル値が渡されている箇所
  - `.get_secret_value()` なしで `SecretStr` フィールドが直接使われている箇所

### 4. PII ログ漏洩
- `Grep` で `logger.info`, `logger.warning`, `logger.error` を検索
- `email_body`, `content`, `body` 等がそのままログに渡されていないか確認
- `mask_for_logging()` の適用漏れを検出

### 5. Kill Switch チェック漏れ
- `src/workers/tasks.py` 内の全タスク関数を検索
- 各タスクの冒頭に `check_kill_switch()` があるか確認

### 6. 非同期違反
- `Grep` で同期的な DB/Redis 呼び出しを検索（`session.execute` が `await` なし等）

## 出力フォーマット

```
## Security Audit Report

### CRITICAL (マージブロッカー)
- ❌ [ファイル:行番号] 説明

### WARNING (修正推奨)
- ⚠️ [ファイル:行番号] 説明

### INFO (参考)
- ℹ️ [ファイル:行番号] 説明

### Summary
- Critical: N件
- Warning: N件
- Score: X/100
```
