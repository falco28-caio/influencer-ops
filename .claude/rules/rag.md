---
paths:
  - "src/services/rag.py"
---

# RAG Gotchas

- チャンク上限は 500 トークン、overlap は 50 トークン — `tiktoken` の `cl100k_base` エンコーディングで計測
- セクション見出し（`#`, `##` 等）でチャンクを分割し、見出しコンテキストをチャンクに含める — 見出し無しの途中分割はリトリーバル精度を大幅に下げる
- ChromaDB のコレクション名は変更禁止 — 既存のベクトルインデックスが失われる
- 埋め込みモデルは `settings.embedding_model`（`text-embedding-3-small`）を使用 — OpenAI のモデルを直接指定しない
- RAG コンテキストには `sources` フィールドを必ず含める — ドラフト生成時に「どの SOP を参照したか」の透明性が必要
- テストでは ChromaDB をモックする — インメモリの `chromadb.Client()` を使うとテスト間で状態がリークする
