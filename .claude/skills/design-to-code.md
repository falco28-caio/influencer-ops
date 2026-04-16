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

```
プロダクト種別：[例: SaaSダッシュボード / モバイルアプリ / ランディングページ]
ビジュアルトーン：[例: clean, minimal, professional]
必要画面：[例: ダッシュボード、設定画面、プロフィール、一覧、詳細]
カラーパレット：[例: Primary #1A42FF, Secondary #A226FF, Tertiary #00BEFF]
対象ユーザー：[例: 20代インフルエンサー / 企業マーケティング担当者]
```

**1-3. バリアント探索**
- Stitchの「Variants」機能で3方向を生成
- Voice Canvasで「もっとミニマルに」「CTAを目立たせて」と調整
- 最大5画面を同時生成して一貫性を確保

**1-4. インタラクティブプロトタイプ確認**
- 「Play」で画面遷移をプレビュー

### Phase 2：DESIGN.md エクスポート（2分）

**2-1. エクスポート実行**
- Stitchのエクスポートボタン → 「DESIGN.md」を選択
- 出力内容：カラートークン、タイポグラフィスケール、スペーシングシステム、コンポーネントパターン

**2-2. DESIGN.mdの配置**
```bash
cp ~/Downloads/DESIGN.md ./DESIGN.md
```

### Phase 3：MCP接続（初回のみ・3分）

**方法A（推奨 — Service Account自動更新）：**
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

初回認証：
```bash
npx @_davideast/stitch-mcp init
```

**方法B（シンプル）：**
MCP設定が面倒な場合、DESIGN.mdの中身をClaude Codeに直接渡すだけでも機能する。

**MCP接続で利用可能なツール：**

| ツール | 機能 |
|---|---|
| `build_site` | スクリーンをルートにマッピングしてサイト構築 |
| `get_screen_code` | 指定スクリーンのHTML/CSSコード取得 |
| `get_screen_image` | スクリーンショットをbase64で取得 |

### Phase 4：Claude Codeで実装（15分〜）

**4-1. 実装指示のテンプレート**

```
このプロジェクトのDESIGN.mdを読み込み、以下の画面を実装してください。

フレームワーク：[React + Tailwind CSS / Next.js / HTML+CSS]
画面：[ダッシュボード、設定、一覧]
優先順位：[1. ダッシュボード → 2. 一覧 → 3. 設定]

要件：
- DESIGN.mdのカラートークン・タイポグラフィを厳密に適用
- レスポンシブ対応（モバイルファースト）
- コンポーネント分割は再利用性を重視
```

**4-2. Stitch MCP接続時の高度な指示**

```
Stitch MCPからプロジェクトID [PROJECT_ID] のスクリーンを取得し、
以下のルートマッピングで実装してください：

/ → ダッシュボード画面
/settings → 設定画面
/list → 一覧画面

get_screen_codeで取得したHTMLをReactコンポーネントに変換し、
DESIGN.mdのトークンでTailwind CSSクラスを適用してください。
```

**4-3. 品質チェックポイント**
- [ ] DESIGN.mdのカラートークンが全て適用されているか
- [ ] タイポグラフィスケールが一貫しているか
- [ ] スペーシングがDESIGN.mdの定義に準拠しているか
- [ ] モバイル表示で崩れないか
- [ ] コンポーネント分割が適切か

---

## B案：品質重視パイプライン（Stitch → Figma → Claude Code）

### Phase 1：Stitchでラピッドプロトタイプ（5〜15分）
→ A案のPhase 1と同じ手順

### Phase 2：Figmaへエクスポート（3分）

**2-1. Stitchからのエクスポート**
- 「Export to Figma」でオートレイアウト・構造化レイヤーを維持したまま転送

**2-2. Figmaでの精緻化作業**

| 作業 | 内容 |
|---|---|
| デザインシステム適用 | Figma Variablesにトークンを登録、全コンポーネントにバインド |
| タイポグラフィ調整 | フォントウェイト、行高、レタースペーシングの微調整 |
| スペーシング統一 | 8px/4pxグリッドへの厳密なスナップ |
| コンポーネント化 | 再利用可能なFigmaコンポーネントとして構造化 |
| レスポンシブ確認 | Auto Layoutで複数ブレークポイントを設計 |
| マイクロインタラクション | プロトタイプモードで遷移・ホバー・ステートを定義 |
| チームレビュー | コメント・フィードバックの収集 |

### Phase 3：Figma MCP接続（初回のみ・5分）

**3-1. Figma Dev Mode MCP Serverの起動**
1. Figmaデスクトップアプリ（ブラウザ版は不可）を起動
2. Preferences (Cmd + ,) → Dev Mode MCP Server → ON
3. ローカルで `http://127.0.0.1:3845/sse` が起動

**3-2. Claude Codeとの接続**
```bash
claude mcp add --transport sse figma-dev-mode-mcp-server http://127.0.0.1:3845/sse
```

**3-3. 利用可能なMCPツールチェーン（実行順序が重要）**

```
1. get_metadata       → ファイル構造・コンポーネント情報の取得
2. get_screenshot     → 選択フレームのビジュアル参照
3. get_code_connect_map → Figmaコンポーネント↔コードのマッピング
4. get_variable_defs  → デザイントークン（色・スペーシング等）の取得
5. コード生成         → 上記コンテキストを全て使って実装
```

ステップを飛ばすと生成品質が大幅に劣化する。必ず順番に実行すること。

### Phase 4：Claude Codeで実装

**4-1. Figma URL指定での実装指示**

```
このFigmaデザインを実装してください：[Figma URL]

フレームワーク：[React + Tailwind CSS]
手順：
1. get_metadataでコンポーネント構造を確認
2. get_screenshotでビジュアル参照を取得
3. get_variable_defsでデザイントークンを取得
4. 取得した情報を全て使ってpixel-perfectに実装

追加要件：
- Figma Variablesのトークン名をCSS変数名にマッピング
- コンポーネントの props はFigmaのバリアントに対応させる
- レスポンシブブレークポイントはFigmaのAuto Layout設定に準拠
```

**4-2. Code Connect設定（任意・チーム開発時推奨）**

```
/create-design-system-rules でコードベースの規約をルール化
/code-connect-components でFigmaコンポーネント↔コードの対応を登録
```

---

## 補足：DESIGN.mdテンプレート（Stitch非使用時の手動作成用）

```markdown
# Design System: [プロダクト名]

## Colors
- Primary: [hex]
- Secondary: [hex]
- Background: [hex]
- Surface: [hex]
- Text Primary: [hex]
- Text Secondary: [hex]
- Border: [hex]
- Error: [hex]
- Success: [hex]

## Typography
- Headings: [フォント名], [ウェイト]
- Body: [フォント名], [ウェイト]
- Caption: [フォント名], [ウェイト]
- Scale: [h1: 32px, h2: 24px, h3: 20px, body: 16px, caption: 12px]

## Spacing
- Base unit: [4px / 8px]
- Scale: [xs: 4px, sm: 8px, md: 16px, lg: 24px, xl: 32px, 2xl: 48px]

## Border Radius
- sm: [4px], md: [8px], lg: [16px], full: [9999px]

## Shadows
- sm: [定義]
- md: [定義]
- lg: [定義]

## Components
### Button
- Primary: [bg, text, border-radius, padding]
- Secondary: [bg, text, border-radius, padding]
- Ghost: [bg, text, border-radius, padding]

### Card
- [bg, border, border-radius, padding, shadow]

### Input
- [bg, border, border-radius, padding, focus-ring]
```

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| Stitch MCPが接続できない | 認証期限切れ | `npx @_davideast/stitch-mcp logout --force` → 再init |
| Figma MCPが見えない | デスクトップアプリ未使用 | ブラウザ版では不可。デスクトップアプリで起動 |
| DESIGN.mdが反映されない | ファイルパスの問題 | プロジェクトルート直下に配置されているか確認 |
| 生成コードの色がズレる | トークン名の不一致 | DESIGN.mdのセマンティック変数名とコード内の変数名を突合 |
| MCP接続後もスクリーンが取れない | プロジェクトIDの指定漏れ | stitch-mcpのview機能でプロジェクト一覧を確認 |
| Figmaからの色が違う | Figma Variablesの未バインド | Figma側でVariablesをコンポーネントに適用済みか確認 |

---

## Claudeの自律行動ルール

1. **パイプライン選択**：ユーザーが指定しない限りA案を提案。品質シグナルを検出したらB案を推奨
2. **DESIGN.md優先**：デザインに関するコード生成時、DESIGN.mdが存在すれば必ず参照
3. **トークン逸脱の検出**：生成コードにDESIGN.md未定義の色・フォント・スペーシングがあれば警告
4. **段階的実装**：全画面一括ではなく、1画面ずつ実装→確認→次の画面の順で進める
5. **Stitch Export活用**：Chrome拡張「Stitch Export」でClaude Code JSON形式のエクスポートも可能であることを必要時に案内
