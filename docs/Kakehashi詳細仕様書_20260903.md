# Kakehashi 詳細仕様書_20260903

**バージョン**: 3.1（運用デバッグ反映版。設計v3.0＋実装後改善の確定仕様）
**作成日**: 2026-09-03
**関連文書**: 全体設計書v3.0／詳細実装計画書／使用説明書／作業報告書（同日付、`docs/` 配下）
**実装**: `src/kakehashi`（v3.0.0）、テスト35件全件通過、systemd常駐

---

## 目次

1. [開発に至った経緯](#1-開発に至った経緯)
2. [用語定義](#2-用語定義)
3. [システム概要・配置](#3-システム概要配置)
4. [機能一覧](#4-機能一覧)
5. [Ingress仕様](#5-ingress仕様)
6. [Canonical中間表現仕様](#6-canonical中間表現仕様)
7. [Egress仕様](#7-egress仕様)
8. [翻訳エンジン仕様](#8-翻訳エンジン仕様)
9. [ストリーミング仕様](#9-ストリーミング仕様)
10. [ログ仕様](#10-ログ仕様)
11. [Web UI仕様](#11-web-ui仕様)
12. [設定仕様](#12-設定仕様)
13. [非機能・運用仕様](#13-非機能運用仕様)
14. [スコープ外・制限事項](#14-スコープ外制限事項)
15. [変更履歴](#15-変更履歴)

---

## 1. 開発に至った経緯

### 1.1 発端：較正済みローカルLLMの日本語破綻

コーディング較正済みローカルLLM（REAP枝刈りモデル等）は英語推論は健全だが日本語生成が破綻する。モデルの重みに触れずに入出力を迂回する方針とし、APIプロキシ層での自動翻訳を選択した。製品名「Kakehashi（かけはし）」は日英の橋渡しの意である。

### 1.2 v1.0：CodeRouter前提のStandalone Relay

当初はCodeRouterの内部機能として翻訳中継を構想した（`coderouter-plugin-translate` 企画）。しかし上流基盤への改造要求・責務混在が問題となり、単独常駐アプリとして分離する決定に至った（経緯文書 `translate-relay_別アプリ化の経緯とポート設計.md`）。

### 1.3 v2.0：CodeRouter非依存化・デュアルIngress・Web UI

- CodeRouter非依存の独立プロキシに確定。併用構成を「クライアント → Kakehashi → CodeRouter → LLM」に固定（逆順ではCodeRouterの動的切替が単一providerに固定され機能しないため）。
- IngressをOpenAI互換／Anthropic互換のデュアル化。原文ローリングログ・翻訳フォールバックチェーン・コード保護を実装。

### 1.4 v3.0：EgressのWeb UI管理化（現行設計基盤）

- 上流転送層（Egress）をWeb UI管理対象に昇格。ハーネスはKakehashiのエンドポイントのみを設定し、上流の所在・プロトコル・キー・モデル・温度等はKakehashi側で一元管理。
- プロバイダー登録フロー（エンドポイント＋プロトコル＋APIキー→モデル一覧取得→選択、手入力救済付き）を規定。EgressもOpenAI／Anthropic双方対応。モデル名はEgress選択値で必ず上書き。

### 1.5 v3.1：運用デバッグによる確定（本書の差分）

実運用（Kilo Code＋LM Studio系Qwen＋CodeRouter）で発覚した障害をログ分析で特定し、以下を仕様に昇格した。詳細は作業報告書D1〜D23。

| # | 事象 | 原因 | 仕様化内容 |
|---|---|---|---|
| 1 | Dashboard 500 | 設定参照パス誤り | 修正（§11） |
| 2 | 空文翻訳の誤応答 | 空白文を裏方へ送信し断り文が混入 | 空文抑止（§8.6） |
| 3 | 長時間無応答切断 | ハーネス側約300秒切断 | SSEキープアライブ（§9.5） |
| 4 | 思考モデルの無応答 | `reasoning_content` 破棄 | 思考過程の透過転送（§9.3） |
| 5 | ファイル未生成 | `tool_calls` デルタ破棄 | 道具呼び出し再送出（§9.4） |
| 6 | 推論ループ | 道具結果の `str()` 破損 | 配列contentの正規抽出（§5.4） |
| 7 | 中国語注釈 | 中国製モデルの母語混入 | 出力言語ガード（§7.4） |
| 8 | 英語コードの表示文 | 道具引数は対象外だった | コード内表示文字列翻訳（§8.7） |

---

## 2. 用語定義

| 用語 | 定義 |
|---|---|
| ハーネス | Kilo Code等のAI利用側ツール。Kakehashiの顧客 |
| Ingress／Egress | ハーネス→Kakehashi面／Kakehashi→上流面。双方OpenAI互換・Anthropic互換 |
| Canonical | プロトコル差を吸収する唯一の中間表現（§6） |
| プロバイダー | 上流の宛先（CodeRouter・llama.cpp・Anthropic API等）。Egress管理対象 |
| バックエンド | 翻訳用LLM。優先順位チェーンで冗長化（Egress冗長化はCodeRouter委譲） |
| ガード | 上流生成言語を縛る付加指示（§7.4） |
| コード内表示文字列 | 道具引数内コードのUI文（print・help・エラー文等）。AST方式で翻訳（§8.7） |

---

## 3. システム概要・配置

### 3.1 標準構成

```
【構成A】ハーネス → Kakehashi(8090) → CodeRouter(8088) → ローカルLLM
【構成B】ハーネス → Kakehashi(8090) → ローカルLLM／クラウドAPI（直接）
```

### 3.2 処理フロー

```
認証 → ingress→canonical → request_id採番 → 翻訳IN(JA→EN)
→ Egress上書き（モデル・params・ガード） → 上流転送
→ 翻訳OUT(EN→JA) → 道具引数翻訳 → ingress形式応答変換 → ログ記録
```

### 3.3 ポート・プロセス

| 項目 | 値 |
|---|---|
| 待受 | `0.0.0.0:8090`（`KAKEHASHI_PORT`／`server.port`で可変） |
| 常駐 | systemd user unit `kakehashi.service`（`Restart=always`） |
| 実行 | `.venv` 分離環境、`kakehashi serve` |

---

## 4. 機能一覧

| ID | 機能 | 状態 |
|---|---|---|
| FR1/FR2 | OpenAI／Anthropic Ingress受理 | 実装済 |
| FR3/FR4 | OpenAI／Anthropic Egress登録・転送 | 実装済 |
| FR5 | 4通り相互変換 | 実装済 |
| FR6/FR7 | JA→EN／EN→JA翻訳 | 実装済 |
| FR8 | 翻訳フォールバック＋全滅パススルー | 実装済（cooldown付き） |
| FR9 | プレースホルダ保護 | 実装済（5種） |
| FR10 | SSE両対応 | 実装済（文バッファ＋思考＋道具再送出＋キープアライブ） |
| FR11 | 翻訳裏方のWeb UI管理 | 実装済（Providers同等フロー） |
| FR12〜FR14 | EgressのWeb UI登録・モデル一覧・モデル設定 | 実装済 |
| FR15 | ローリングログ | 実装済（4フィールド拡張） |
| EX1 | 思考過程の透過転送 | v3.1追加 |
| EX2 | 道具呼び出しの再送出 | v3.1追加 |
| EX3 | 出力言語ガード | v3.1追加（既定有効） |
| EX4 | コード内表示文字列翻訳 | v3.1追加（既定有効・Pythonのみ） |
| EX5 | 空文翻訳抑止 | v3.1追加 |

---

## 5. Ingress仕様

### 5.1 エンドポイント

| Method／Path | 入力 | 備考 |
|---|---|---|
| `POST /v1/chat/completions`（`/chat/completions` 別名） | OpenAI | stream時はSSE |
| `POST /v1/messages` | Anthropic | 同上。`max_tokens` 欠落時は4000補完＋警告 |
| `GET /v1/models` | — | アクティブEgressへ素通し。失敗時 `{"data":[]}` |
| `GET /healthz` | — | 常に200 `{"status":"ok","version"}` |
| `GET /healthz/upstream` | — | 到達確認。失敗時も200＋`degraded` |
| `GET /ui/` | — | 管理SPA |

### 5.2 認証

`server.api_key` 非空時のみ要求。`Authorization: Bearer`／`x-api-key`／`X-API-Key` のいずれか合致で通過。不一致は401（OpenAI形式・Anthropic形式の各様式）。

### 5.3 受理フィールド

- OpenAI: `model/messages/tools/tool_choice/stream/temperature/max_tokens/top_p/stop`。`content` 配列の `text` のみ翻訳対象。`developer`→`system`畳み。未知フィールドは `_passthrough_openai` に退避しOpenAI Egress時のみ再合流。
- Anthropic: `model/system/messages/tools/tool_choice/stream/max_tokens/temperature/top_p/stop_sequences`。`system` は先頭systemに畳み出力時に復元。`tool_use`／`tool_result` は構造保持。

### 5.4 道具結果の忠実度（v3.1）

`role: tool` の配列contentはテキスト抽出（未知部はJSON保持・画像等はプレースホルダ化）し、Python `str()` による引用符破損を起こさない。入口構造（役割列・content種別・tool_calls数）は常駐ログへ記録する（本文なし）。

---

## 6. Canonical中間表現仕様

```python
Message(role: system|user|assistant|tool, content: str | [TextBlock|ToolUseBlock|ToolResultBlock])
CanonicalRequest(messages, tools[{name,description,input_schema}], tool_choice, stream, params, model, raw_ingress)
CanonicalResponse(text, tool_uses, stop_reason[end_turn|max_tokens|tool_use|error], usage)
```

- `stop_reason` 正規化表: `stop↔end_turn`、`length↔max_tokens`、`tool_calls↔tool_use`。未知値は素通し＋警告。
- 変換は `ingress→canonical` と `canonical→egress` の独立関数で4通りを合成。

---

## 7. Egress仕様

### 7.1 送信先解決・URL正規化

- `egress.active_provider` の指す1件へ転送。Egress自体のフォールバックは持たない（CodeRouter委譲）。
- Base URL末尾の `/chat`・`/chat/completions`・`/messages` を剥がして正規化（`/v1/chat/` 形式の救済）。
- 認証: OpenAIは `Bearer`、Anthropicは `x-api-key`＋`anthropic-version: 2023-06-01`。解決順は `api_key_env`→`api_key`→空。

### 7.2 モデル上書き・パラメータ適用

- 転送時に `model` をアクティブ選択値で必ず上書き（`auto` 時のみ要求値維持）。`model_override{requested,applied}` をログ記録。
- `temperature/max_tokens/top_p/stop` は設定値ありの場合に適用。`merge_policy=override`（既定）はKakehashi優先、`client_wins` はハーネス優先。
- `extra_body` はOpenAI互換時のみ合流。Anthropic時は無視＋警告。Anthropic必須の `max_tokens` は設定→既定4000で補完。

### 7.3 モデル一覧取得

`GET {base}/models`（10秒）。OpenAIは `data[].id`、Anthropicは同APIを `x-api-key` で列挙。失敗は `{models:[], error}` で返し登録を阻害しない（手入力救済）。

### 7.4 出力言語ガード（v3.1）

- 転送直前にシステム末尾へ英語専用指示を付加（ハーネスのシステムには不干渉）。既定文は英語のみ・中国語禁止。
- `translation.prompts.output_guard` で編集可、空で無効。ログに `output_guard` 有効旗を記録。

---

## 8. 翻訳エンジン仕様

### 8.1 処理フロー

```
対象抽出（userのTextBlockのみ）→ 言語判定 → 保護 → 翻訳（チェーン）
→ 検証（空・欠損） → 復元 → ログ用件数集計
```

応答側はassistant本文、道具引数は§8.7の経路で処理する。

### 8.2 言語検出

CJK文字比率が `cjk_threshold`（既定0.1）以上で日本語判定。Request側は日本語含むuser文のみ翻訳。Response側は英語前提で本文を試行（`enabled=false` で全停止）。

### 8.3 保護パターン

`code_block`→`inline_code`→`url`→`uuid`→`path` の順に `__KXH_n__` 置換。`{...}` 形式欄はコード文字列経路で別途保護。欠損数は `placeholder_fail` に計上。

### 8.4 バックエンド呼出・フォールバック

- OpenAI互換 `chat/completions` に `temperature: 0.1` 固定でPOST。
- `on_status`（既定429/500/502/503/504）・タイムアウトで次点へ。失敗裏方は `cooldown_s`（既定60秒）隔離。`max_attempts_per_backend=1`。
- 空応答は失敗扱い。全滅時は原文パススルー＋警告。

### 8.5 バッチ翻訳

短文複数は `[KXH-i]` 付番で1呼出に集約しマーカーで復元。不一致時は個別再試行。全滅時は原文。

### 8.6 空文抑止（v3.1）

空・空白のみ入力は裏方を呼ばず原文返却（裏方の空文断り文の混入防止）。

### 8.7 コード内表示文字列翻訳（v3.1）

- 対象: 応答側の道具引数内文字列。print文・help・エラー文・docstring等のUI文をEN→JA。
- PythonソースはAST解析で文字列リテラルだけをexact置換し再解析検証（失敗時は原文）。f-string全体・複数行リテラルは保守的に除外。
- 識別子・SQL・URL・パス・`{...}` 欄・日本語由来文は除外。`min_length`（既定8）・空白・英字含有で判定。
- 非Pythonは厳密フィルタ通過分のみ全体翻訳。`translation.code_strings{enabled,min_length}` で制御（既定有効）。
- ログに `code_strings` 件数を記録。

### 8.8 プロンプト

`translation.prompts{ja2en,en2ja,output_guard}` で管理。Web UIで編集・既定復元。翻訳呼出は設定値を参照する。

---

## 9. ストリーミング仕様

### 9.1 基本方針

- Request側は非streaming先行翻訳（stream時も coroutine をジェネレータ内で実行）。
- Response側は文区切り（`。．！？!?\\n`、8文字以上）でバッファし文完成ごとに翻訳して送出。残部は終端時フラッシュ。
- tool_useデルタは翻訳せず再送出（§9.4）。

### 9.2 SSE送受信形式

- 送出（OpenAI）: `chat.completion.chunk`＋`[DONE]`。送出（Anthropic）: `message_start`→`content_block_start`→`content_block_delta`→`message_stop`。
- 受信（OpenAI）: `choices[0].delta.content` 連結。受信（Anthropic）: `content_block_delta(delta.text)` 抽出。

### 9.3 思考過程の透過（v3.1）

- OpenAI Egressの `reasoning_content`、Anthropic Egressの `thinking_delta` を抽出。
- OpenAI Ingressには `reasoning_content` チャンクとして無翻訳で即時転送（直接接続時と同等表示）。
- Anthropic Ingressには署名なし制約のためプレーンテキストとして転送。
- ログに `reasoning_chars` を記録。

### 9.4 道具呼び出しの再送出（v3.1）

- OpenAI形式断片（index/id/name/arguments）・Anthropic形式（tool_use開始＋input_json_delta）を蓄積。
- 終端時に完全形で再送出。OpenAI側はtool_calls＋`finish_reason: tool_calls`、Anthropic側はtool_useブロック列＋`stop_reason: tool_use`。道具引数には§8.7を適用後に送出。
- ログに `tool_calls` 件数を記録。

### 9.5 キープアライブ（v3.1）

- 翻訳待ち・上流待ちが `KEEPALIVE_S = 15` 秒停滞するごとに `: ping ...` コメント行を送出（SSE規格上無視される）。
- 上流受信はキューポンプ＋タイムアウト付き待機で実装。切断時は翻訳・ポンプ両タスクをキャンセル。

---

## 10. ログ仕様

### 10.1 翻訳ログ

- 配置 `~/.local/share/kakehashi/logs/translation.jsonl`、10MB×5世代ローテ、ON/OFF可。原文平文注意。

| フィールド | 内容 |
|---|---|
| `ts/request_id` | 時刻・追跡ID |
| `ingress_protocol/egress_protocol/egress_provider` | 経路 |
| `model_override{requested,applied}` | モデル置換記録 |
| `direction/phase/stream` | 方向・段階・stream区分 |
| `translate_backend_used/translate_fallbacks/placeholder_fail` | 裏方・切替・保護欠損 |
| `request_original/request_translated` | 日本語原文→英訳後 |
| `response_upstream/response_final` | 翻訳前応答→日本語訳後 |
| `reasoning_chars/tool_calls/code_strings/output_guard` | v3.1拡張 |
| `latency_ms{translate_in,upstream,translate_out,total}` | 内訳（stream時はupstream/translate_out=-1） |
| `upstream_error` | 上流失敗時のみ |

### 10.2 常駐ログ・運用記録

- `journalctl --user -u kakehashi` に入口構造（役割列・content種別・tool_calls数）・転送ペイロード構造（件数・役割列・文字数・tool_calls数）をINFO記録（本文なし）。

---

## 11. Web UI仕様

- 同一ポート `/ui` の静的SPA（Vanilla JS＋fetch、CDNなし）。認証時は `X-API-Key` を `localStorage` 保持。
- 日本語メニュー: ダッシュボード／接続先LLM／翻訳モデル／翻訳指示／ログ／サーバー。見出しは虹グラデーション、上部濃紺・下部薄水色。

| 画面 | 内容 |
|---|---|
| ダッシュボード | 状態カード・接続先詳細・翻訳チェーン順位・レイテンシ棒グラフ・最近10件 |
| 接続先LLM | 一覧・3ステップ登録・モデル一覧取得・接続確認・使用中切替・モデル設定 |
| 翻訳モデル | 同一3ステップ登録・編集・接続確認（固定文「こんにちは」） |
| 翻訳指示 | 日英・英日・出力言語ガード編集、コード内翻訳ON/OFF・最小文字数、既定復元 |
| ログ | ON/OFF・上限・最新エントリ（平文注意書き） |
| サーバー | ホスト・ポート・APIキー（空＝なし） |

REST API（`/api/config/*`）: `full/reload/server/providers(+fetch-models,+{id}/test,/active)/backends(+fetch-models,/reorder,+{id}/test)/prompts(+/reset)/code-strings/logging(+/tail)/dashboard`。

---

## 12. 設定仕様

`~/.config/kakehashi/config.yaml`（600権限、手動編集可、`reload` APIで再読込）。

```yaml
server: {host: "0.0.0.0", port: 8090, api_key: ""}
egress:
  active_provider: "coderouter"
  providers:
    - {id, name, protocol: openai|anthropic, base_url, api_key: "",
       api_key_env: "", model, timeout_s: 300,
       params: {merge_policy: override|client_wins, temperature?,
                max_tokens?, top_p?, stop?, extra_body: {}}}
  # chain: []  # 将来予約
translation:
  enabled: true
  default_pair: [ja, en]
  cjk_threshold: 0.1
  protect_patterns: [code_block, inline_code, url, uuid, path]
  backends:
    - {id, name: "", protocol: openai|anthropic, base_url, model,
       api_key: "", api_key_env: "", timeout_s: 30, enabled: true}
  retry: {on_status: [429,500,502,503,504], on_timeout: true,
          max_attempts_per_backend: 1, cooldown_s: 60}
  prompts: {ja2en, en2ja, output_guard}
  code_strings: {enabled: true, min_length: 8}
  rules: []  # 将来予約
logging: {translation_log_enabled: true,
  translation_log_dir: "~/.local/share/kakehashi/logs",
  translation_log_max_mb: 10, translation_log_backups: 5}
webui: {enabled: true, path: "/ui"}
```

- 秘密解決順: `api_key_env`→`api_key`→空。API応答では `***` マスク。
- バリデーション: active存在・id重複禁止・`http(s)://` 始まり。

---

## 13. 非機能・運用仕様

| 項目 | 仕様 |
|---|---|
| 言語／依存 | Python 3.12+／fastapi・uvicorn・httpx・pyyaml・pydantic（jinja2不採用） |
| 常駐 | systemd user unit、`Restart=always`、再起動後自動起動 |
| 性能特性 | 翻訳2回分のレイテンシ増（内訳計測可）。巨大入力時は裏方呼出が律速 |
| 復旧 | Kakehashi停止時はハーネスを上流直結に戻す（1行変更） |
| セキュリティ | LAN前提・既定認証なし。公開時はAPIキー＋リバースプロキシ認証 |

---

## 14. スコープ外・制限事項

- システムプロンプトの翻訳なし（ガード付加は行う）。`tool_calls`／`tool_result` の構造本体は翻訳しない（表示文字列のみ§8.7で処理）。
- 完全スキーマ互換なし。主要項目限定、未知フィールドは警告＋素通し／破棄。
- Egress冗長化なし（CodeRouter委譲）。code_stringsはPythonのみ。f-string全体・複数行リテラルは除外。
- 創作・文脈依存文の翻訳劣化があり得る。原文ログで定期目視すること。

---

## 15. 変更履歴

| 版 | 変更 |
|---|---|
| v1.0 | CodeRouter前提のStandalone Relay |
| v2.0 | CodeRouter非依存化・デュアルIngress・Web UI・ローリングログ |
| v3.0 | EgressのWeb UI管理化・デュアルEgress・登録フロー・モデル設定GUI化・構成固定 |
| v3.1（本書） | 空文抑止・キープアライブ・思考透過・道具再送出・道具結果忠実度・出力言語ガード・コード内表示文字列翻訳・ダッシュボード拡充・ログ4＋4フィールド拡張・Backends登録フロー統一・URL正規化 |
