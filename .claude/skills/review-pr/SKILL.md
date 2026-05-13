---
name: review-pr
description: PRをセキュリティ・品質・ドメイン知識の観点で徹底レビューする
argument-hint: "[PR番号]"
user-invocable: true
allowed-tools: "Read, Grep, Glob, Bash(git *), Agent"
---

# PR Review Agent

PR #$ARGUMENTS を以下の5観点でレビューしてください。

## 1. セキュリティチェック
- [ ] ガードレール: 新しいアウトバウンド処理に `GuardrailService.check_content()` が適用されているか
- [ ] LLM入力: ユーザーコンテンツに `sanitize_for_prompt()` が適用されているか
- [ ] Secrets: ハードコードされた API キー・トークンがないか
- [ ] PII: ログ出力に `mask_for_logging()` が適用されているか
- [ ] Injection: 新しい正規表現やSQL文にインジェクション脆弱性がないか

## 2. コード品質
- [ ] `from __future__ import annotations` がすべての .py ファイルにあるか
- [ ] 全関数に型アノテーションがあるか
- [ ] SQLAlchemy 2.0 スタイル（`mapped_column`）を使用しているか
- [ ] Pydantic v2 (`from_attributes = True`) を使用しているか
- [ ] structlog で構造化ログ（f-string 禁止）を使用しているか

## 3. アーキテクチャ整合性
- [ ] Services がステートレスか（状態は Redis/DB に保存）
- [ ] Adapters が `BaseAdapter` を継承しているか
- [ ] Config が `settings` シングルトン経由か（ハードコード禁止）
- [ ] Workflow の状態遷移が `WorkflowEngine` 経由か

## 4. テストカバレッジ
- [ ] 新機能に対応するテストが追加されているか
- [ ] 外部 API がモックされているか（実 API 呼び出し禁止）
- [ ] エッジケース（空入力、タイムアウト、エラー）がカバーされているか

## 5. ドメイン特有
- [ ] Autopilot の信頼度閾値が適切か（0.9 未満で auto-send しない）
- [ ] Kill switch チェックが新しい Worker タスクに含まれているか
- [ ] RAG チャンキングがトークン上限（500）を超えていないか

## 出力フォーマット

各カテゴリの結果を以下の形式で報告:
- ✅ 問題なし
- ⚠️ 軽微な指摘（修正推奨）
- ❌ ブロッキング（マージ前に修正必須）

最後に総合判定: **Approve** / **Request Changes** / **Comment Only**
