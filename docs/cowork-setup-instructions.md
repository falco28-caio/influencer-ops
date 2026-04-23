# Codex グローバル設定セットアップ指示書

## 目的

design-to-codeスキルとClaude Code設定を、全セッションで使えるようにグローバル配置する。

## 前提条件

- macOS上でClaude Code（ローカルCLI版）を実行中であること
- `~/.claude/` ディレクトリが存在すること（Claude Codeインストール済みなら自動作成される）

---

## 手順

### Step 1: ディレクトリ作成

```bash
mkdir -p ~/.claude/skills
```

### Step 2: design-to-code スキルを配置

以下の内容で `~/.claude/skills/design-to-code.md` を作成してください。

```markdown
---
name: design-to-code
description: UIデザインから開発実装までの標準ワークフロースキル。Google Stitch → Claude Codeの速度重視パイプライン（A案）と、Stitch → Figma → Claude Codeの品質重視パイプライン（B案）を実行する。必ずこのスキルを使うべき場面：「UIを作りたい」「デザインから実装」「Stitchで」「DESIGN.md」「デザインシステム」「画面設計」「プロトタイプから実装」「フロントエンド」「Figma Make」「デザイン→コード」「ワイヤーフレームから」「UIデザイン」「画面を作って」「アプリのUI」「ランディングページ」「ダッシュボード設計」といったキーワードを含むリクエスト。デフォルトはA案（速度重視）、品質にこだわる案件ではB案を提案すること。
---

# UIデザイン → 開発実装 ワークフロースキル

## 概要

UIデザインの構想から本番コードへの実装までを、最小フリクションで実行する標準手順書。
2つのパイプラインを使い分ける。

| パイプライン | ルート | 用途 | 所要時間目安 |
|---|---|---|---|
| **A案（デフォルト）** | Stitch → DESIGN.md + MCP → Claude Code | 速度重視。MVP、社内ツール | 30分〜2時間 |
| **B案** | Stitch → Figma精緻化 → Figma MCP → Claude Code | 品質重視。クライアント納品、ブランド要件あり、複雑UI | 半日〜1日 |

### 判断基準：A案 or B案？

- ユーザーが明示しない場合、**A案をデフォルト提案**する
- 以下のシグナルがあればB案を提案：
  - 「クライアント向け」「納品」「ブランドガイドライン準拠」
  - 「pixel-perfect」「細部にこだわりたい」「チームレビューが必要」
  - 複雑なインタラクション（マイクロアニメーション、条件分岐UI）
  - デザインシステムがFigma Variablesで既に構築済み

---

## A案：速度重視パイプライン（Stitch → Claude Code）

### Phase 1：Stitchでデザイン生成（5〜15分）

**1-1. Stitchにアクセス**
- URL: https://stitch.withgoogle.com
- モード選択：アプリ→「App」、ウェブ→「Web」
- モデル：品質重視なら Gemini Pro、探索段階なら Flash

**1-2. プロンプト設計**

プロダクト種別、ビジュアルトーン、必要画面、カラーパレット、対象ユーザーを含める。

**1-3. バリアント探索**
- Stitchの「Variants」機能で3方向を生成
- Voice Canvasで調整
- 最大5画面を同時生成して一貫性を確保

**1-4. インタラクティブプロトタイプ確認**
- 「Play」で画面遷移をプレビュー

### Phase 2：DESIGN.md エクスポート（2分）

- Stitchのエクスポートボタン → 「DESIGN.md」を選択
- プロジェクトルート直下に配置: `cp ~/Downloads/DESIGN.md ./DESIGN.md`

### Phase 3：MCP接続（初回のみ・3分）

推奨設定（.mcp.json または settings.json）：
```json
{
  "mcpServers": {
    "stitch": {
      "command": "npx",
      "args": ["@_davideast/stitch-mcp", "proxy"]
    }
  }
}
```

初回認証: `npx @_davideast/stitch-mcp init`

MCP接続で利用可能なツール: build_site, get_screen_code, get_screen_image

### Phase 4：Claude Codeで実装（15分〜）

DESIGN.mdを読み込み、カラートークン・タイポグラフィを適用して実装。
1画面ずつ実装→確認→次の画面の順で進める。

---

## B案：品質重視パイプライン（Stitch → Figma → Claude Code）

### Phase 1：Stitchでラピッドプロトタイプ
→ A案のPhase 1と同じ

### Phase 2：Figmaへエクスポート
- 「Export to Figma」でオートレイアウト・構造化レイヤーを維持したまま転送
- Figma側でデザインシステム適用、タイポグラフィ調整、スペーシング統一、コンポーネント化、レスポンシブ確認、チームレビューを実施

### Phase 3：Figma MCP接続（初回のみ・5分）
1. Figmaデスクトップアプリ → Preferences → Dev Mode MCP Server → ON
2. `claude mcp add --transport sse figma-dev-mode-mcp-server http://127.0.0.1:3845/sse`

ツールチェーン（順番厳守）：
1. get_metadata → 2. get_screenshot → 3. get_code_connect_map → 4. get_variable_defs → 5. コード生成

### Phase 4：Claude Codeで実装
- Figma URL指定でpixel-perfect実装
- Code Connect設定でFigmaコンポーネント↔コードの対応を登録

---

## Claudeの自律行動ルール

1. パイプライン選択：ユーザーが指定しない限りA案を提案。品質シグナルを検出したらB案を推奨
2. DESIGN.md優先：デザインに関するコード生成時、DESIGN.mdが存在すれば必ず参照
3. トークン逸脱の検出：生成コードにDESIGN.md未定義の色・フォント・スペーシングがあれば警告
4. 段階的実装：全画面一括ではなく、1画面ずつ実装→確認→次の画面の順で進める
```

### Step 3: ~/.claude/CLAUDE.md にグローバルルールを追記

既に `~/.claude/CLAUDE.md` が存在する場合は、以下のセクションが含まれていることを確認してください。
存在しない場合は新規作成してください。

追記するセクション：

```markdown
## Design System

When a `DESIGN.md` file exists at the project root, treat it as the single source of truth for all UI/frontend work.
All UI components must use tokens defined in DESIGN.md (colors, typography, spacing).
```

### Step 4: 配置確認

以下のコマンドで配置を確認：

```bash
echo "=== Skills ==="
ls -la ~/.claude/skills/
echo ""
echo "=== CLAUDE.md ==="
head -5 ~/.claude/CLAUDE.md 2>/dev/null || echo "(not found)"
echo ""
echo "=== Skill content check ==="
head -3 ~/.claude/skills/design-to-code.md 2>/dev/null || echo "(not found)"
```

期待される出力：
- `~/.claude/skills/design-to-code.md` が存在すること
- `~/.claude/CLAUDE.md` にDesign Systemセクションがあること

### Step 5: 動作テスト

新しいClaude Codeセッションを開き、以下を入力して `/design-to-code` スキルが認識されるか確認：

```
UIを作りたい
```

Claude Codeがdesign-to-codeスキルを呼び出し、A案/B案の選択を提案すれば成功。

---

## 注意事項

- `~/.claude/skills/` に配置したスキルは全プロジェクトのClaude Codeセッションで有効になる
- プロジェクト固有のルール（コードスタイル、DB設定など）は各リポジトリの `CLAUDE.md` に書く
- MCP設定をグローバルにしたい場合は `~/.claude/settings.json` の `mcpServers` に記述する
