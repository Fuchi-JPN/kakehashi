# Kakehashi｜日英⇄英日 自動翻訳APIプロキシ 全体設計書_20260903

**バージョン**: 3.0（上流転送層のWeb UI管理・マルチプロトコルEgress対応への改訂）
**作成日**: 2026-09-03
**ステータス**: 実装着手可能

---

## 0. 改訂履歴

| 版 | 変更点 |
|---|---|
| v1.0 | CodeRouter前提のStandalone Relay設計 |
| v2.0 | CodeRouter非依存化・OpenAI/AnthropicデュアルIngress・Web UI新設・原文ローリングログ |
| **v3.0（本書）** | **上流転送層（Egress）をWeb UI管理対象に昇格**。プロバイダー登録フロー（エンドポイント＋プロトコル＋APIキー→モデル一覧取得→選択）を規定。EgressのプロトコルはOpenAI互換に加え**Anthropic互換もサポート**。モデル関連設定（温度・max_tokens等）をWeb UIへ集約。CodeRouter併用時の構成を「クライアント → Kakehashi → CodeRouter → ローカルLLM」に確定 |

### 0.1 v3.0の設計判断

- **IngressとEgressのプロトコルは独立**: KakehashiのIngressはOpenAI互換／Anthropic互換の双方を受け付ける（v2.0継続）。加えてv3.0では**Egressも双方をサポート**する。クライアント→Kakehashi→上流の各段でプロトコルが一致している必要はなく、Kakehashi内部のCanonical表現を経由して相互変換される。
- **Egress設定のWeb UI集約**: Kilo Code等のハーネスは**Kakehashiのエンドポイントだけ**を設定すればよい。上流プロバイダーの所在（エンドポイント・プロトコル・APIキー・モデル）はすべてKakehashiのWeb UIで管理する。ハーネス側の再設定なしに上流を切り替えられることが本改訂の核心的価値である。
- **CodeRouter併用構成の固定**: CodeRouterを併用する場合の配列は **「クライアント → Kakehashi → CodeRouter → ローカルLLM」** に固定する。CodeRouterは動的なプロバイダー／モデル切り替え機能を持つため、上流の到達点として責務を負わせるにはKakehashiが前段にいる必要がある。逆順（CodeRouterが前段）ではCodeRouterのルーティング先としてKakehashiを登録することになり、CodeRouterの動的切り替えの利点をプロキシ経路全体に適用できないため、システムとして成立しない。

---

## 1. 概要

### 1.1 プロダクト定義

**Kakehashi**（読み: かけはし）は、日英⇄英日の自動翻訳をAPIプロキシとして透過的に挟み込む単独常駐アプリである。

- **解く課題**: コーディング較正済みローカルLLM（REAP枝刈りモデル等）は英語推論は健全だが日本語生成が破綻する。モデルの重みに触れず、入出力をプロキシ層で翻訳して迂回する。
- **役割分担**: Kakehashiは「翻訳」＋「統一エンドポイント」を提供し、ハーネス（Kilo Code等）はKakehashiだけを指す。上流のプロバイダー／モデル管理はKakehashiのWeb UIに集約する。

### 1.2 主要特徴

| 特徴 | 内容 |
|---|---|
| **デュアルプロトコル（両方向）** | Ingress・EgressともにOpenAI互換／Anthropic互換をサポート。任意の組み合わせで透過変換 |
| **Egress Web UI管理** | プロバイダー登録フロー（エンドポイント→プロトコル→APIキー→モデル一覧取得→選択）をGUIで完結 |
| **モデル設定のGUI化** | 温度・max_tokens等のモデルパラメータをWeb UIで設定・保存 |
| **独立翻訳フォールバック** | 翻訳用LLMを優先順位リストで登録。429/5xx/タイムアウトで次点へ。全滅時は原文パススルー |
| **原文ローリングログ** | 翻訳前/後ペアをJSONLローテーション保存（既定ON） |
| **コード保護** | プレースホルダ方式でコードブロック・URL・ID等を保護 |

---

## 2. 配置構成

### 2.1 標準構成

```
【構成A】CodeRouter併用（現行運用・trim等が必要な間）
  ハーネス → Kakehashi(8090) → CodeRouter(8088) → ローカルLLM
                              ※CodeRouterの動的プロバイダー切替を
                                最後段のルーティング層として活用

【構成B】CodeRouterなし（将来の標準姿勢）
  ハーネス → Kakehashi(8090) → ローカルLLM or クラウドプロバイダー（直接）
```

構成Aにおいて「Kakehashiが前段」である理由（再掲・固定決定事項）:
- CodeRouterの動的プロバイダー／モデル切り替えは「CodeRouterが受信したリクエスト」にのみ作用する。Kakehashiが後段（CodeRouter→Kakehashi）だと、CodeRouterの切替対象が「Kakehashiという単一 provider 」に固定されてしまい、切替機構が機能しない。
- Kakehashiが前段であれば、CodeRouterには「翻訳済み英語リクエストをどのバックエンド（プロバイダー／モデル）へ流すか」の最終決定を任せられ、両者の責務（翻訳 vs ルーティング）がきれいに分離する。

### 2.2 ハーネス側の設定

Kilo Code等には以下のみを設定する。上流の変更はKakehashi側で吸収され、ハーネスの再設定は不要。

| ハーネス側設定項目 | 値 |
|---|---|
| APIエンドポイント | `http://<host>:8090/v1` |
| APIキー | KakehashiでAPIキーを有効化した場合のみ（既定不要） |
| モデル名 | **任意の文字列でよい**（KakehashiがEgress側モデルに上書きする。§4.6参照） |

---

## 3. 要件定義（v3.0改訂後）

### 3.1 機能要件（FR）

| ID | 要件 |
|---|---|
| FR1 | OpenAI Chat Completions API（`/v1/chat/completions`）をIngressとして受理できる |
| FR2 | Anthropic Messages API（`/v1/messages`）をIngressとして受理できる |
| FR3 | **OpenAI互換プロトコルのプロバイダーをEgressとして登録・転送できる** |
| FR4 | **Anthropic互換プロトコルのプロバイダーをEgressとして登録・転送できる** |
| FR5 | Ingress↔Egressのプロトコル組み合わせは任意（4通り全て相互変換で透過対応） |
| FR6 | `role=user` の自然文テキストを英語へ翻訳してから上流へ送る（JA→EN） |
| FR7 | 上流の英語応答を日本語へ翻訳してからクライアントへ返す（EN→JA） |
| FR8 | 翻訳用バックエンドを優先順位付き複数登録。429/5xx/タイムアウトで次点へ自動フォールバック。全滅時は原文パススルー＋警告ログで継続 |
| FR9 | コードブロック・インラインコード・URL・UUID等をプレースホルダ保護する |
| FR10 | SSEストリーミングに対応する（Ingress/Egress双方・両プロトコル） |
| FR11 | Web UI経由で翻訳バックエンドのリスト・順序・有効/無効を変更でき、再起動なしで反映される |
| FR12 | **Web UI経由でEgressプロバイダーの登録・編集・削除・切替ができる** |
| FR13 | **プロバイダー登録フローで「モデル一覧取得」により実在モデルを選択できる**（§5.3） |
| FR14 | **モデル関連パラメータ（temperature, max_tokens, top_p, その他）をWeb UIで設定できる** |
| FR15 | 翻訳前/翻訳後テキストをペアでローリングログへ保存する（ON/OFF可、既定ON） |

### 3.2 非機能要件（NFR）

| ID | 要件 |
|---|---|
| NFR1 | Python 3.12+ / venv / systemd常駐。Docker非必須 |
| NFR2 | 新規依存は `fastapi` `uvicorn` `httpx`（必要なら `jinja2` のみ検討可・不要なら不採用）に抑える |
| NFR3 | 上流基盤（ローカルLLM、CodeRouter等）の改造を一切要求しない |
| NFR4 | レイテンシ増は許容（コスト優先）。ただし計測・観測可能とする |
| NFR5 | セキュリティ最小限。Listen既定 `0.0.0.0`。認証は任意APIキーのみ（既定OFF） |
| NFR6 | 設定はファイル永続化（YAML/JSON）＋実行時リロード。手動編集も許容 |

### 3.3 スコープ外

| 項目 | 扱い |
|---|---|
| システムプロンプトの翻訳 | しない |
| `tool_calls` / `tool_result` の構造本体 | 翻訳しない（プロトコル変換は行う） |
| 完全なスキーマ相互互換 | 主要項目（messages/tools/streaming/基本パラメータ）に限定。未知フィールドは警告ログのうえ素通しまたは破棄 |

---

## 4. システムアーキテクチャ

### 4.1 全体構成

```
 OpenAI互換ハーネス                Anthropic互換ハーネス
   (Kilo Code等)                      (Claude Code等)
        │ POST /v1/chat/completions        │ POST /v1/messages
        ▼                                  ▼
┌────────────────────────────────────────────────────┐
│                  Kakehashi (0.0.0.0:8090)           │
│ ┌──────────────────────────────────────────────┐  │
│ │ Ingress層                                     │  │
│ │  OpenAI /v1/chat/completions                  │  │
│ │  Anthropic /v1/messages                       │  │
│ │  → Canonical表現に正規化                      │  │
│ └──────────────────┬───────────────────────────┘  │
│                    ▼                               │
│ ┌──────────────────────────────────────────────┐  │
│ │ 翻訳エンジン                                  │  │
│ │  抽出→保護→翻訳(フォールバックチェーン)       │  │
│ │  →検証→復元→原文ログ保存                      │  │
│ └──────────────────┬───────────────────────────┘  │
│                    ▼                               │
│ ┌──────────────────────────────────────────────┐  │
│ │ Egress層（上流転送）─────────────────────    │  │
│ │  アクティブプロバイダーへ転送                  │  │
│ │   protocol: openai  → POST {base}/chat/completions │
│ │   protocol: anthropic → POST {base}/messages │  │
│ │  モデルパラメータ適用（Web UI設定値で上書き）   │  │
│ └──────────────────┬───────────────────────────┘  │
│                    │                               │
│ ┌──────────────────────────────────────────────┐  │
│ │ Web UI (/ui)                                  │  │
│ │  Backends(翻訳) / Providers(Egress) / Rules   │  │
│ │  / Logging / Server / Dashboard               │  │
│ └──────────────────────────────────────────────┘  │
│ ┌──────────────────────────────────────────────┐  │
│ │ ローリングログ translation.jsonl              │  │
│ └──────────────────────────────────────────────┘  │
└────────────────────┬───────────────────────────────┘
                     │  （アクティブEgressプロバイダーへ）
        ┌────────────┼────────────────┐
        ▼            ▼                ▼
   CodeRouter   ローカルLLM直接    クラウドAPI
   (8088)       (llama.cpp等)    (Anthropic等)
```

### 4.2 プロトコル変換マトリクス

| ＼ | Egress: OpenAI | Egress: Anthropic |
|---|---|---|
| **Ingress: OpenAI** | ほぼ素通し（モデル名・パラメータ上書きのみ） | OpenAI→Anthropic変換 |
| **Ingress: Anthropic** | Anthropic→OpenAI変換 | ほぼ素通し |

変換はCanonical表現を経由するため、変換ロジックは `ingress→canonical` と `canonical→egress` の独立関数として実装し、組み合わせで4通りすべてをカバーする。responseも逆方向に同じ経路で戻す。

### 4.3 Canonical表現

```python
CanonicalRequest = {
    "messages": [{"role": "system|user|assistant|tool", "content": str | [Block]}],
    "tools": [...],          # 正規化済みツール定義
    "tool_choice": ...,      # 正規化
    "stream": bool,
    "params": {              # Egressのモデル設定で上書きされる前の要求値
        "max_tokens": int | None,
        "temperature": float | None,
        "top_p": float | None,
        "stop": [...],
    },
    "model": str,            # ハーネス指定値（Egress適用時に上書き）
}
```

### 4.4 変換対応表（主要項目）

| Canonical | OpenAI | Anthropic |
|---|---|---|
| `role: system` | messages先頭のsystemまたはdeveloper | トップレベル `system` |
| `tool_use`（assistant発） | `tool_calls[]` | `tool_use` ブロック |
| `tool_result` | `role: tool` | `tool_result` ブロック |
| `stop_reason` | `finish_reason` | `stop_reason`（値の写像: end_turn↔stop 等） |
| ストリーミング | `chat.completion.chunk` | event型SSE（message_start/content_block_delta等） |

### 4.5 Egressフォールバックについて

- **翻訳バックエンドのフォールバックチェーン**（FR8）はKakehashiが自前で持つ（確定済み）。
- **Egressプロバイダー自体のフォールバック**はv3.0では**持たない**。上流の冗長化が必要な場合はCodeRouterをEgressに指す構成（構成A）で実現する責務分離とする。
  - 理由: 責務の明確化（Kakehashi=翻訳、CodeRouter=ルーティング）と実装複雑性の抑制。
  - 将来、CodeRouterを廃止したいが冗長化は必要、という要求が出た場合のために、`egress.chain` 拡張は設定スキーマ上予約しておく（§7）。

### 4.6 モデル名の上書き

ハーネスが送信する `model` フィールドは、KakehashiがEgressへ転送する際に**アクティブプロバイダーの選択モデルで必ず上書き**する。これにより、Kilo Code等がモデル名の存在を前提としたバリデーションを行う場合でも、ハーネス側には任意値を入れておけばよい。上書きは翻訳ログに `model_override: {requested, applied}` として記録する。

---

## 5. Web UI 設計

### 5.1 技術構成

| 項目 | 決定 |
|---|---|
| 提供形式 | FastAPIがSPA静的ファイルを `/ui` で配信（本体と同一ポート8090） |
| フロントエンド | ビルドレス構成（Vanilla JS + fetch）。CDN不可のオフライン環境を考慮し完全自ホスト |
| 設定API | `/api/config/*`（REST）。変更は設定ファイルへ永続化＋実行時リロード |
| 認証 | サーバAPIキーと共通（設定時のみ `X-API-Key` 必須） |

### 5.2 画面構成

```
/ui
 ├─ Dashboard        稼働状況・24h統計（翻訳数／翻訳フォールバック数／原文ログ量）
 ├─ Providers (Egress)   ★v3.0新設：上流プロバイダー管理
 │   ├─ 一覧（名前・プロトコル・エンドポイント・選択モデル・Active切替ラジオ）
 │   ├─ 登録/編集ウィザード（§5.3）
 │   ├─ モデル設定（temperature/max_tokens/top_p等、§5.4）
 │   └─ [Test] 疎通確認
 ├─ Translation Backends  翻訳用モデル管理（D&D並替・Test）
 ├─ Translation Rules     言語ペア・CJK閾値・保護パターン（将来：ルールベース詳細設定）
 ├─ Logging               原文ログON/OFF・上限・最新エントリ表示
 └─ Server                host/port・APIキー
```

### 5.3 プロバイダー登録フロー（FR13の詳細）

```
 [+ Add Provider] を押下
        │
        ▼
 Step 1: 基本情報
   ┌──────────────────────────────────┐
   │ Name:      [ my-local-server  ]  │
   │ Protocol:  (•) OpenAI-compatible │
   │            ( ) Anthropic         │
   │ Base URL:  [ http://...:8088 ]   │  ※プロトコル選択で末尾パスの
   │ API Key:   [ ****             ]  │   既定表示が切替（/v1 等）
   │            [ env:VAR_NAME から ] │  ※env参照も可
   └──────────────────────────────────┘
        │ [モデル一覧取得] ボタン ← ここが登録フローの肝
        ▼
 Step 2: モデル一覧取得
   ・protocol=openai    → GET {base}/models（OpenAIのモデル一覧API）
   ・protocol=anthropic → GET {base}/models（Anthropic Models API：
                          GET https://api.anthropic.com/v1/models、
                          x-api-key + anthropic-version ヘッダ）
   ・取得成功 → ドロップダウンにモデルID一覧を表示
   ・失敗 → エラー表示（HTTP status/本文冒頭）＋手入力フィールドを
            並行して常時提供（一覧非対応サーバの救済。llama.cppの
            /v1/models 実装差異等を想定）
        │
        ▼
 Step 3: モデル選択と保存
   ・ドロップダウンからモデル選択
   ・（任意）モデルパラメータ初期値（§5.4）
   ・[Save] → 設定ファイルに永続化 → 即時有効
```

**モデル一覧取得の実装上の注意**:

| プロトコル | エンドポイント | 認証ヘッダ | 備考 |
|---|---|---|---|
| OpenAI互換 | `GET {base}/models` | `Authorization: Bearer <key>` | ローカルLLM（llama.cpp, LM Studio, Ollama等）の実装差に注意。レスポンスの `data[].id` を列挙。パース失敗時は「手入力してください」にフォールバック |
| Anthropic互換 | `GET {base}/models` | `x-api-key` / `anthropic-version: 2023-06-01` | 非公式互換サーバ（CodeRouter等）が未実装の場合あり。同様に手入力へフォールバック |
| タイムアウト | 10秒固定 | — | 一覧取得失敗は登録をブロックしない（Step 3で手入力可） |

### 5.4 モデル設定（FR14の詳細）

プロバイダー毎に以下を持つ。全て任意・未設定項目はハーネスの要求値を尊重する。

| フィールド | 型 | 既定 | 動作 |
|---|---|---|---|
| `temperature` | float | 未設定 | 設定時は常にこの値で上書き |
| `max_tokens` | int | 未設定 | 同上 |
| `top_p` | float | 未設定 | 同上 |
| `stop` | list[str] | 未設定 | 同上 |
| `extra_body`（OpenAI互換のみ） | JSON | `{}` | プロバイダー固有パラメータ（例: llama.cppのchat_template_kwargs等）をそのままマージ |
| `merge_policy` | `"override" \| "client_wins"` | `override` | override: Kakehashi設定を優先。client_wins: ハーネス指定があればそちらを優先 |

Anthropic互換Egressの場合、OpenAI固有フィールド（`logprobs` 等）は警告のうえ変換時に破棄する。

### 5.5 将来のルールベース詳細設定（拡張予約）

Translation Rules画面は下記を**同画面の拡張として追加**できる構造とする。設定スキーマは `translation_rules: []`（空配列＝無適用）として先行定義し、後方互換を保つ。

- 正規表現置換ルール（翻訳前/後の双方に適用可能）
- 用語集（`source_term → target_term` の強制写像）
- 翻訳プロンプトテンプレート編集
- 条件付きルール（正規表現マッチ時のみ翻訳方向変更等）

---

## 6. ロギング設計

### 6.1 翻訳原文ログ（v2.0から継続・フィールド拡張）

- 配置: `~/.local/share/kakehashi/logs/translation.jsonl`
- ローテーション: 既定 10MB×5世代。Web UIで変更可
- **1行の構造（v3.0拡張版）**:

```json
{
  "ts": "2026-09-03T12:34:56Z",
  "request_id": "uuid",
  "ingress_protocol": "openai",
  "egress_protocol": "openai",
  "egress_provider": "coderouter",
  "model_override": {"requested": "gpt-4o", "applied": "qwen3.8-reap384"},
  "direction": "ja2en",
  "phase": "request",
  "translate_backend_used": "tb-openrouter",
  "translate_fallbacks": 1,
  "placeholder_fail": 0,
  "original": "この関数をリファクタリングして ```python ...``` を保ったまま",
  "translated": "Refactor this function while keeping ```python ...``` intact",
  "latency_ms": {"translate_in": 412, "upstream": 3200, "translate_out": 388, "total": 4000},
  "stream": false
}
```

- 注意: 原文がそのまま残る。外部共有時は内容確認。Web UIから即OFF可能。

---

## 7. 設定スキーマ（v3.0確定版）

`~/.config/kakehashi/config.yaml`（Web UIからの変更先でもある。手動編集可）

```yaml
server:
  host: "0.0.0.0"
  port: 8090
  api_key: ""                 # 空＝認証なし

egress:
  active_provider: "coderouter"     # id参照。Web UIで切替
  providers:
    - id: "coderouter"
      name: "CodeRouter (local)"
      protocol: "openai"            # openai | anthropic
      base_url: "http://127.0.0.1:8088/v1"
      api_key_env: ""               # or api_key: "..."
      model: "auto"                 # CodeRouterの動的切替に委ねる場合の値
      timeout_s: 300
      params:
        merge_policy: "override"
        # temperature: 0.2
        # max_tokens: 8192
        extra_body: {}
    - id: "local-llama"
      name: "llama.cpp direct"
      protocol: "openai"
      base_url: "http://127.0.0.1:8081/v1"
      api_key_env: ""
      model: "qwen3.8-reap384"
      timeout_s: 300
      params:
        merge_policy: "override"
        max_tokens: 8192
    - id: "claude-api"
      name: "Anthropic API"
      protocol: "anthropic"
      base_url: "https://api.anthropic.com"
      api_key_env: "ANTHROPIC_API_KEY"
      model: "claude-sonnet-4-5-20250929"
      timeout_s: 120
      params:
        max_tokens: 8192
  # chain: []                      # 将来拡張予約（v3.0では未使用）

translation:
  enabled: true
  default_pair: ["ja", "en"]
  cjk_threshold: 0.1
  protect_patterns: [code_block, inline_code, url, uuid, path]
  backends:
    - id: "tb-local"
      name: "Local translate model"
      base_url: "http://127.0.0.1:1234/v1"
      model: "translategemma-12b"   # 例。実在モデルは運用時確定
      api_key_env: ""
      timeout_s: 30
      enabled: true
    - id: "tb-openrouter"
      base_url: "https://openrouter.ai/api/v1"
      model: "<要現行確認>"
      api_key_env: "OPENROUTER_API_KEY"
      timeout_s: 45
      enabled: true
  retry:
    on_status: [429, 500, 502, 503, 504]
    on_timeout: true
    max_attempts_per_backend: 1
    cooldown_s: 60
  rules: []                         # 将来のルールベース詳細設定用に予約

logging:
  translation_log_enabled: true
  translation_log_dir: "~/.local/share/kakehashi/logs"
  translation_log_max_mb: 10
  translation_log_backups: 5

webui:
  enabled: true
  path: "/ui"
```

### 7.1 APIキーの管理方針

- `api_key_env: "VAR_NAME"` 形式での環境変数参照を第一推奨（設定ファイルに秘密を書かない運用）。
- `api_key: "..."` 直接記述も許容するが、Web UIはその場合ファイルパーミッション `600` を強制し、画面表示は `•••` マスク＋再入力式とする。
- 設定ファイル全体の保存時パーミッションは `600`。

---

## 8. ポート・配置

| 用途 | 値 | メモ |
|---|---|---|
| Kakehashi本体＋Web UI | `0.0.0.0:8090`（既定） | `KAKEHASHI_PORT` で可変 |
| ヘルス | `/healthz`（自身）、`/healthz/upstream`（アクティブEgressへの到達性） | upstream失敗でも落とさずdegraded応答 |
| CodeRouter併用時 | Kakehashi 8090 → CodeRouter 8088 → （WebUI 8089等は独立） | 配列は固定（§2.1） |

systemdユニット（EgressをKakehashi管理下に置いたため、CodeRouterへの依存関係は「併用時のみ任意」）:

```ini
[Unit]
Description=Kakehashi Translation Proxy
After=network-online.target

[Service]
ExecStart=%h/kakehashi/.venv/bin/kakehashi serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

---

## 9. 実装ロードマップ（v3.0版）

| Phase | 内容 | 完了条件 |
|---|---|---|
| **P0** | 雛形・設定ロード・ヘルス | `/healthz` 200 |
| **P1** | OpenAI Ingress→OpenAI Egressの素通し中継（モデル名上書き込み） | OpenAIクライアント往復成功 |
| **P2** | Canonical表現＋Anthropic Ingress→OpenAI Egress変換 | Anthropicクライアントから往復成功 |
| **P3** | Anthropic Egress対応（4通り全組合せ） | 全プロトコル組合せで往復成功 |
| **P4** | 翻訳エンジン（保護・翻訳・検証・復元）＋翻訳フォールバックチェーン | コード混入保護・全滅パススルー確認 |
| **P5** | ストリーミング（両プロトコルIngress×Egress） | SSE逐次翻訳が破綻しない |
| **P6** | 原文ローリングログ | ローテーション動作確認 |
| **P7** | Web UI MVP：Providers登録フロー（**モデル一覧取得**・選択・モデル設定） | GUI登録→即時反映。一覧取得失敗時の手入力救済を確認 |
| **P8** | Web UI残り画面（翻訳Backends/Rules/Logging/Server/Dashboard） | 全設定のGUI変更→再起動なし反映 |
| **P9** | systemd常駐化・LAN公開（0.0.0.0）・Kilo Code実機接続 | ハーネス設定をKakehashiのみにして運用成立 |
| **P10** | 1週間運用観測（レイテンシ・フォールバック率・品質） | 安定運用の判断 |

---

## 10. テスト計画（v3.0差分強調）

| # | 観点 | 内容 | 期待 |
|---|---|---|---|
| 1 | プロトコル4通り | OpenAI/Anthropic × OpenAI/Anthropic のIngress×Egress | 全組合せで往復成功 |
| 2 | モデル上書き | ハーネスが存在しないモデル名を送信 | Egress側の選択モデルに置換され上流成功。ログにoverride記録 |
| 3 | モデル一覧取得 | OpenAI互換・Anthropicそれぞれで[モデル一覧取得] | 一覧表示。非対応サーバでは手入力に誘導 |
| 4 | モデルパラメータ | merge_policyの両値で温度等を検証 | override時にKakehashi設定優先 |
| 5 | 翻訳往復 | 日本語→英語→日本語の意味保持 | 破綻なく往復 |
| 6 | 翻訳フォールバック・全滅 | 無効化・全無効化 | 次点切替／パススルー＋ログ |
| 7 | コード保護 | コード・URL混在 | 非破壊復元 |
| 8 | ストリーミング | 両プロトコルのSSE | 逐次翻訳・終端正常 |
| 9 | CodeRouter併用構成 | ハーネス→Kakehashi→CodeRouter→LLM | CodeRouterの動的切替（プロファイル切替等）が機能 |
| 10 | 設定即時反映 | Web UIでプロバイダー追加→直後にリクエスト | 再起動なしで新プロバイダー使用可 |
| 11 | ログローテーション | 上限超過 | 世代退避 |
| 12 | 復旧性 | Kakehashi停止→ハーネス切替 | 直接接続への切替手順が成立 |

---

## 11. リスクと注意点

| リスク | 内容と対策 |
|---|---|
| レイテンシ増 | 翻訳2回分。ログの `latency_ms` 分解（translate_in/upstream/translate_out）で切り分け観測 |
| プロトコル変換の不完全性 | OpenAI↔Anthropic変換は主要項目限定。未知フィールドは警告ログ。実運用で遭遇次第、変換表を拡張 |
| モデル一覧APIの実装差 | ローカルLLMサーバや非公式互換サーバは `/models` 未実装・形式差あり。**一覧取得失敗≧想定内**として手入力救済を必須機能とする（FR13の内側で担保） |
| 翻訳品質 | 創作・文脈依存文は劣化し得る。原文ログで定期目視 |
| 0.0.0.0既定の公開面 | LAN前提。インターネット露出は避け、必要時はAPIキー有効化またはリバースプロキシ層で認証 |
| 秘密情報のUI経由漏洩 | APIキーはマスク表示・再入力式。configは600 |
| Kakehashi障害時の系全体停止 | ハーネスを上流直結へ戻す手順（エンドポイント1行変更）を運用ドキュメント化 |

---

## 12. 将来拡張

| 構想 | 現設計での準備 |
|---|---|
| ルールベース詳細翻訳 | `translation.rules: []` 予約済み・Rules画面拡張 |
| Egressフォールバックチェーン | `egress.chain` 予約済み（現状はCodeRouterに委譲の方針維持） |
| 言語ペア拡張 | `default_pair` の構成変更＋言語検出強化で対応可能な構造 |
| 追加プロトコル（Gemini等） | Canonical経由の変換設計により追関数の追加で対応可 |

---

## 13. 決定事項サマリ（v3.0確定）

| 項目 | 決定 |
|---|---|
| プロダクト名 | **Kakehashi** |
| 形態 | 独立常駐APIプロキシ（上流非依存） |
| Ingress | OpenAI互換 `/v1/chat/completions` ＋ Anthropic互換 `/v1/messages` |
| **Egress** | **OpenAI互換／Anthropic互換の双方をWeb UIで登録・選択可能** |
| **プロバイダー登録フロー** | **エンドポイント＋プロトコル＋APIキー → モデル一覧取得 → 選択（失敗時手入力救済）** |
| **モデル設定** | **Web UIで管理（温度・max_tokens等・merge_policy）** |
| ハーネス側設定 | Kakehashiエンドポイントのみ。モデル名はKakehashiが上書き |
| CodeRouter併用構成 | **ハーネス → Kakehashi → CodeRouter → ローカルLLM**（固定） |
| 翻訳フォールバック | Kakehashi自身が優先順位チェーンを持つ |
| Egress冗長化 | v3.0ではCodeRouter委譲。`egress.chain` で将来拡張予約 |
| ネットワーク | Listen既定 `0.0.0.0:8090`、セキュリティ最小（任意APIキー） |
| ログ | 翻訳前/後ペアJSONLローリングログ（既定ON・10MB×5） |
| 依存 | Python 3.12+ / FastAPI / uvicorn / httpx |

---
