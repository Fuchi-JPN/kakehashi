# Kakehashi 詳細実装計画書

**対象設計書**: `docs/Kakehashi｜日英⇄英日 自動翻訳APIプロキシ 全体設計書_20260903.md` v3.0
**作成日**: 2026-09-03
**ステータス**: 実装着手可能
**目的**: v3.0設計書の全要件（FR1〜FR15 / NFR1〜NFR6）を、ファイル単位・関数単位・API単位で実装可能な粒度に分解する。

---

## 0. 方針・前提

### 0.1 設計書の核心の再確認

1. **Ingress/Egressともデュアルプロトコル**: OpenAI互換 / Anthropic互換 × OpenAI互換 / Anthropic互換の4通りをCanonical経由で相互変換する（FR1〜FR5）。
2. **EgressはWeb UI管理**: ハーネスは `http://<host>:8090/v1` のみを見る。上流の所在・プロトコル・キー・モデル・温度等はすべてKakehashi側で保持・上書きする（FR12〜FR14）。
3. **CodeRouter併用構成は固定**: `ハーネス → Kakehashi(8090) → CodeRouter(8088) → LLM`。逆順は採用しない。
4. **翻訳フォールバックは自前、Egress冗長化は持たない**: 翻訳バックエンドのみ優先順位チェーン＋全滅パススルー（FR8）。Egressの冗長化はCodeRouter委譲。`egress.chain` は予約のみ。
5. **依存最小**: `fastapi` `uvicorn` `httpx` + `pyyaml` + `pydantic`（+標準libのみ）。`jinja2` は不採用を第一案とする（SPAは静的ファイル配信のみのため不要）。

### 0.2 非目標（スコープ外の再掲）

- システムプロンプト翻訳なし、`tool_calls`/`tool_result` 本体翻訳なし、完全スキーマ互換なし（未知フィールドは警告ログ＋素通し/破棄）。

### 0.3 実装順序の原則

- P0→P1でまず「素通しプロキシ」として成立させ、P2→P3で変換、P4で翻訳、P5でストリーミング、P6以降でログ・UI・運用へ進む。各Phaseは単独で起動・検証可能にする。

---

## 1. リポジトリ構成・技術選定

### 1.1 ディレクトリ構成（確定案）

```
kakehashi/
├── pyproject.toml
├── README.md
├── .gitignore
├── docs/
│   ├── Kakehashi｜日英⇄英日 自動翻訳APIプロキシ 全体設計書_20260903.md  # v3.0設計書
│   └── Kakehashi詳細実装計画書_20260903.md                              # 本書
├── src/kakehashi/
│   ├── __init__.py              # __version__ = "3.0.0"
│   ├── __main__.py              # python -m kakehashi serve
│   ├── cli.py                   # argparse: serve / config validate / version
│   ├── app.py                   # FastAPI factory create_app()
│   ├── config.py                # 設定ロード/保存/バリデーション/ホットリロード
│   ├── models_canonical.py      # CanonicalRequest/Response等のdataclass/pydantic
│   ├── protocol_openai.py       # OpenAI⇄Canonical 変換（request/response/stream）
│   ├── protocol_anthropic.py    # Anthropic⇄Canonical 変換
│   ├── egress.py                # アクティブプロバイダーへの転送クライアント
│   ├── translate/
│   │   ├── __init__.py
│   │   ├── detector.py          # JA含有判定（CJK閾値）
│   │   ├── protector.py         # プレースホルダ保護/復元
│   │   ├── client.py            # 翻訳バックエンド呼び出し（OpenAI互換chat/completions）
│   │   ├── engine.py            # 抽出→保護→翻訳→検証→復元のオーケストレーション
│   │   └── prompts.py           # JA→EN / EN→JA プロンプトテンプレート
│   ├── translate_log.py         # translation.jsonl ローリングライト
│   ├── webui/
│   │   ├── __init__.py
│   │   ├── api.py               # /api/config/* REST
│   │   └── static/              # ビルドレスSPA
│   │       ├── index.html
│   │       ├── app.js
│   │       └── style.css
│   └── routes/
│       ├── __init__.py
│       ├── openai_ingress.py    # POST /v1/chat/completions
│       ├── anthropic_ingress.py # POST /v1/messages
│       ├── health.py            # GET /healthz, /healthz/upstream
│       └── models_proxy.py      # GET /v1/models（任意: ハーネス互換用素通し）
├── tests/
│   ├── test_convert_oai_anthropic.py
│   ├── test_protector.py
│   ├── test_detector.py
│   ├── test_egress_params.py
│   ├── test_fallback.py
│   ├── test_logging.py
│   ├── test_config.py
│   └── test_e2e_protocols.py
└── systemd/
    └── kakehashi.service
```

### 1.2 `pyproject.toml` 骨子

```toml
[project]
name = "kakehashi"
version = "3.0.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "pyyaml>=6.0",
  "pydantic>=2.7",
]

[project.scripts]
kakehashi = "kakehashi.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "strict"  # 必要なら pytest-asyncio 追加、なければanyio不使用でTestClientのみ

[build-system]
requires = ["setuptools>=61"]
```

- NFR2は `fastapi/uvicorn/httpx` に限定する旨だが、`yaml` と `pydantic` は実質必須のため追加採用とする。`jinja2` は不採用（静的SPAのみ）。
- テストは標準 `pytest` + `fastapi.testclient.TestClient` で開始し、ストリーミングE2Eのみ `httpx.AsyncClient(ASGITransport)` を使う。

### 1.3 実行形態

- `python -m kakehashi serve [--config ~/.config/kakehashi/config.yaml] [--port 8090]`
- 環境変数: `KAKEHASHI_PORT`（既定8090）、`KAKEHASHI_CONFIG`（既定 `~/.config/kakehashi/config.yaml`）。
- systemdは設計書§8のユニットを `systemd/kakehashi.service` として repo 管理する。

---

## 2. 設定管理（`config.py`）

### 2.1 ファイル配置・権限

| 項目 | 決定 |
|---|---|
| パス | `~/.config/kakehashi/config.yaml`（`KAKEHASHI_CONFIG` で上書き可） |
| 初回起動 | ファイル不在時は §7 の既定値（coderouter/openai 1件＋translation 2件の雛形）で自動生成 |
| 権限 | 保存時 `0o600` 強制（`os.chmod`）。読込時に `0o600` でなければ警告ログ（起動は継続） |
| 永続化 | Web UI `PUT /api/config/*` ごとに YAML 全文再書き＋インメモリ原子交換 |
| 手動編集 | 許容。`SIGHUP` または `/api/config/reload` で再読込。パース失敗時は旧設定を維持＋エラー応答 |

### 2.2 Pydanticモデル（フィールド確定）

```python
class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8090
    api_key: str = ""

class EgressParams(BaseModel):
    merge_policy: Literal["override", "client_wins"] = "override"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    extra_body: dict = {}          # OpenAI互換のみ有効

class EgressProvider(BaseModel):
    id: str                        # [a-z0-9-]{1,64}
    name: str
    protocol: Literal["openai", "anthropic"]
    base_url: str                  # 末尾スラッシュ除去して正規化
    api_key: str = ""              # 平文（600で保護）
    api_key_env: str = ""          # 両指定時は api_key_env を優先
    model: str                     # 実モデルID or "auto"（CodeRouter委譲時）
    timeout_s: int = 300
    params: EgressParams = EgressParams()

class EgressConfig(BaseModel):
    active_provider: str
    providers: list[EgressProvider] = []
    # chain: list = []  # 将来予約。パースは許容するが不使用

class TranslateBackend(BaseModel):
    id: str
    name: str = ""
    base_url: str
    model: str
    api_key: str = ""
    api_key_env: str = ""
    timeout_s: int = 30
    enabled: bool = True

class TranslateRetry(BaseModel):
    on_status: list[int] = [429, 500, 502, 503, 504]
    on_timeout: bool = True
    max_attempts_per_backend: int = 1
    cooldown_s: int = 60

class TranslationConfig(BaseModel):
    enabled: bool = True
    default_pair: list[str] = ["ja", "en"]
    cjk_threshold: float = 0.1
    protect_patterns: list[str] = ["code_block", "inline_code", "url", "uuid", "path"]
    backends: list[TranslateBackend] = []
    retry: TranslateRetry = TranslateRetry()
    rules: list = []  # 将来予約

class LoggingConfig(BaseModel):
    translation_log_enabled: bool = True
    translation_log_dir: str = "~/.local/share/kakehashi/logs"
    translation_log_max_mb: int = 10
    translation_log_backups: int = 5

class WebUIConfig(BaseModel):
    enabled: bool = True
    path: str = "/ui"

class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    egress: EgressConfig
    translation: TranslationConfig = TranslationConfig()
    logging: LoggingConfig = LoggingConfig()
    webui: WebUIConfig = WebUIConfig()
```

バリデーション追加事項:

- `egress.active_provider` は `providers[].id` に存在すること。
- `providers[].id` 重複禁止、`base_url` は `http(s)://` 始まり。
- `api_key` と `api_key_env` の同時指定は許容（解決順: `api_key_env` → `api_key` → `""`）。解決関数は `resolve_secret(provider) -> str` に一元化し、ログ・API応答に平文を出さない。

### 2.3 スレッドセーフ・ホットリロード

```python
class ConfigStore:
    def __init__(self, path): ...
    def get(self) -> AppConfig: ...          # ロック下で参照返却（コピー不要なイミュータブル運用）
    def update(self, mutator) -> AppConfig: ...  # 変更→validate→原子交換→YAML保存(600)
    def reload_from_disk(self) -> AppConfig: ...
```

- `threading.RLock` で保護。リクエスト経路は `store.get()` のスナップショットを使う。
- Web UIからの更新は `update()` 経由のみ。保存失敗時はメモリも巻き戻す。

---

## 3. Canonical表現（`models_canonical.py`）

設計書§4.3をそのまま型化する。プロトコル固有の差を吸収する唯一の中間表現。

```python
@dataclass
class TextBlock: text: str
@dataclass
class ToolUseBlock: id: str; name: str; input: dict
@dataclass
class ToolResultBlock: tool_use_id: str; content: str | list; is_error: bool = False

ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock

@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock]

@dataclass
class CanonicalRequest:
    messages: list[Message]
    tools: list[dict] = field(default_factory=list)   # 正規化済み（name/description/input_schema）
    tool_choice: Any = None
    stream: bool = False
    params: dict = field(default_factory=dict)        # max_tokens/temperature/top_p/stop
    model: str = ""                                    # ハーネス要求値（Egressで上書き）
    raw_ingress: Literal["openai", "anthropic"] = "openai"

@dataclass
class CanonicalResponse:
    text: str                                          # assistant最終テキスト（tool_use除く）
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    stop_reason: str = "end_turn"                      # 正規化値: end_turn|max_tokens|tool_use|error
    usage: dict = field(default_factory=dict)
```

- `stop_reason` 正規化表: `stop↔end_turn`, `length↔max_tokens`, `tool_calls↔tool_use`。未知値は素通し＋警告ログ。
- `tool` roleはOpenAIの `role:tool` とAnthropicの `tool_result` ブロックを統一するための内部role。

---

## 4. Ingress層（`routes/`）

### 4.1 エンドポイント一覧

| Method/Path | 入力プロトコル | 処理 |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI | OpenAI→Canonical→（翻訳）→Egress→Canonical→OpenAIで応答。`stream=true`時はSSE |
| `POST /v1/messages` | Anthropic | Anthropic→Canonical→（翻訳）→Egress→Canonical→Anthropicで応答。`stream=true`時はSSE |
| `GET /v1/models` | — | アクティブEgressの `GET {base}/models` を素通しプロキシ。失敗時は `{"data":[]}`＋警告（ハーネスのモデル列挙救済）。認証不要 |
| `GET /healthz` | — | `{"status":"ok","version":...}` 常に200 |
| `GET /healthz/upstream` | — | アクティブEgressへ軽量到達確認（`GET {base}/models` 10秒）。成功200 / 失敗200＋`{"status":"degraded",...}`（落とさない） |
| `GET /ui/*`, `/api/config/*` | — | Web UI（§9） |

- Base path注意: ハーネス設定は `http://host:8090/v1` のため、FastAPIルートは `/v1/...` で切る。`POST /chat/completions`（`/v1`なし）への別名も追加し、末尾スラッシュ差も吸収する。
- 認証: `server.api_key` 非空時のみ `Authorization: Bearer <key>`（OpenAI側）または `x-api-key`（Anthropic側）を要求。Web UIの `/api/*` は `X-API-Key` ヘッダで同一キー検証。不一致は `401`。

### 4.2 OpenAI Ingress受理仕様（`protocol_openai.py`）

受理する主要フィールド:

```
model, messages[{role, content(str|[ {type:text,image_url...} ]), tool_calls, tool_call_id}],
tools[{type:function, function:{name,description,parameters}}],
tool_choice, stream, temperature, max_tokens, top_p, stop
```

- `content` 配列内の `type:text` のみ翻訳対象。`image_url` 等は保持・翻訳対象外。
- `role:developer` はCanonical `system` に畳む（出力時は `system` に戻す。OpenAI側の往復に支障なし）。
- 未知フィールドは破棄せず `params["_passthrough_openai"]` に退避し、EgressがOpenAIの場合のみ再マージ（Anthropic Egress時は警告ログ＋破棄）。

### 4.3 Anthropic Ingress受理仕様（`protocol_anthropic.py`）

受理する主要フィールド:

```
model, system(str|[block]), messages[{role:user|assistant, content:str|[block]}],
tools[{name,description,input_schema}], tool_choice, stream, max_tokens(required),
temperature, top_p, stop_sequences
```

- `max_tokens` 必須（Anthropic仕様）のため、欠落時は `4000` を補完＋警告ログ（ハーネス互換性のため）。
- `system` はCanonical先頭 `system` Messageに畳む。出力時は再びトップレベル `system` に戻す。
- `tool_use` / `tool_result` ブロックは構造保持でCanonical化し、翻訳対象外。

### 4.4 共通パイプライン（両Ingressで同一）

```
1. 認証 → 2. ingress→canonical → 3. request_id採番(uuid4) → 4. 翻訳IN (JA→EN, §6)
→ 5. Egress転送 (§5, モデル上書き・params適用) → 6. 翻訳OUT (EN→JA)
→ 7. canonical→ingress応答変換 → 8. 翻訳ログ書込 (§8) → 応答返却
```

- 例外はすべて `request_id` 付きJSONエラーで返却（OpenAI形式 `{"error":{...}}` / Anthropic形式 `{"type":"error","error":{...}}` に合わせる）。
- `latency_ms{translate_in, upstream, translate_out, total}` を計測しログへ。

---

## 5. Egress層（`egress.py`）

### 5.1 送信先解決

```python
def active_provider(cfg: AppConfig) -> EgressProvider
def egress_url(p: EgressProvider) -> str:
    base = p.base_url.rstrip("/")
    return f"{base}/chat/completions" if p.protocol=="openai" else f"{base}/messages"
    # base_urlに既に /v1 が含まれる運用（設計書例 http://127.0.0.1:8088/v1）を想定し、
    # 末尾が既に /chat/completions or /messages なら重複付加しない正規化を行う
```

- CodeRouter（OpenAI互換）を指す場合 `model: "auto"` を許容し、そのまま上流へ送る（CodeRouter側の動的切替に委譲）。

### 5.2 モデル名上書き・パラメータ適用（FR14）

```python
def apply_egress_overrides(canon: CanonicalRequest, p: EgressProvider) -> CanonicalRequest:
    requested = canon.model
    if p.model != "auto":
        canon.model = p.model
    # params適用
    for k in ["temperature","max_tokens","top_p","stop"]:
        v = getattr(p.params, k)
        if v is not None:
            if p.params.merge_policy=="override" or canon.params.get(k) is None:
                canon.params[k] = v
    # log用に (requested, applied) を返却
```

- `extra_body` はEgressがOpenAIの場合のみトップレベルへマージ（Anthropic時は無視＋警告ログ）。
- Anthropic Egressで `max_tokens` 未解決の場合はプロバイダー設定値→既定 `4000` の順で補完（Anthropic API必須のため）。

### 5.3 認証ヘッダ

| Egress protocol | ヘッダ |
|---|---|
| openai | `Authorization: Bearer <secret>`（secret空なら付与しない） |
| anthropic | `x-api-key: <secret>` ＋ `anthropic-version: 2023-06-01` |

### 5.4 非ストリーミング転送

- `httpx.AsyncClient(timeout=provider.timeout_s)` でPOST。`on_status=[429,5xx]`・タイムアウト時は1回だけリトライせず即エラー化する（Egressフォールバックなしの設計通り。CodeRouter側に委譲）。
- 上流エラーは ingress 形式に包み直して返却し、`translate_log` に `upstream_error` として記録する。

### 5.5 ストリーミング転送（§7と連携）

- Egressへ `stream:true` でPOSTし、SSEを `httpx.stream` で逐次受信→Canonicalデルタへ正規化→文バッファ翻訳（§7.3）→Ingress形式SSEで再送出する。
- Egress(OpenAI)→Ingress(Anthropic)等の異種結合も同一のCanonicalデルタ経由で吸収する。

---

## 6. 翻訳エンジン（`translate/`）

### 6.1 処理フロー（`engine.py`）

```
translate_request(canon) / translate_response(canon):
  1. 対象抽出: role=user の TextBlockのみ（system/assistant/toolは対象外）
  2. 言語判定(detector): 日本語含むか？（request: JA含む→翻訳 / response: 常にEN→JAを試行※6.2）
  3. 保護(protector.protect): コード/URL/UUID/パスを __KXH_n__ に置換
  4. 翻訳(client.translate with fallback chain): 保護済みテキストを翻訳
  5. 検証: 空応答・原文同一・プレースホルダ欠損を検出→失敗扱いで次点/パススルー
  6. 復元(protector.restore): プレースホルダを原文に戻す（欠損数は placeholder_fail としてログ）
```

### 6.2 言語検出（`detector.py`）

```python
def is_japanese(text: str, threshold: float) -> bool:
    # CJK（ひらがな・カタカナ・漢字・ハングル・CJK統合漢字）の文字比率 >= threshold でJA判定
    # 既定 threshold=0.1（config.translation.cjk_threshold）
```

- Request側: `is_japanese(text)` がTrueの `user` メッセージのみJA→EN。
- Response側: 上流応答は英語前提のため、JA文字を含む場合でもEN→JA翻訳を試行する（英語中に日本語引用が混ざるケースの実害は軽微）。ただし `translation.enabled=false` 時は全スキップ。

### 6.3 保護パターン（`protector.py`）

| 名前 | 正規表現（骨子） | 備考 |
|---|---|---|
| `code_block` | ```` ```.*?``` ````（DOTALL） | 最優先で保護 |
| `inline_code` | `` `[^`\n]+` `` | |
| `url` | `https?://[^\s)>\]]+` | |
| `uuid` | `[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}` | |
| `path` | `(?:\/[\w.\-]+){2,}\|(?:[A-Za-z]:\\(?:[^\\\n]+\\)*[^\\\n]*)` | 誤爆しやすいため最後に適用 |

- 置換形式: `__KXH_{index}__`。`protect()` は `(protected_text, table)` を返し、`restore()` で逆写像する。`config.protect_patterns` に列挙されたもののみ適用。
- 翻訳後にプレースホルダが欠損・変形していた場合、残存分のみ復元し `placeholder_fail` カウントをログへ。

### 6.4 翻訳バックエンドクライアント（`client.py`）

- プロトコル: OpenAI互換 `POST {base_url}/chat/completions`（`{base_url}` は `/v1` 込みを想定）に `{"model": backend.model, "messages": [...], "temperature": 0.1, "stream": false}` でPOST。
- 認証: `api_key_env`→`api_key`解決値を `Bearer` で付与。
- フォールバックチェーン:

```python
async def translate(text, direction, cfg) -> (translated, backend_id_used, fallbacks):
    for backend in enabled_backends_in_order:
        if cooldown中: skip
        try: 呼出（timeout=backend.timeout_s）
        except (status in retry.on_status / timeout):
            fallbacks+=1; cooldown登録; continue
        検証OKなら return
    return (原文, None, fallbacks)  # 全滅時は原文パススルー＋警告ログ
```

- `cooldown_s`（既定60秒）: 失敗バックエンドを一時スキップするインメモリ `dict[backend_id, until]`。
- `max_attempts_per_backend=1`（v3.0固定）。

### 6.5 プロンプト（`prompts.py`）

```text
JA→EN system:
You are a precise Japanese-to-English translator for software engineering chat.
Translate ONLY the user text to natural English. Preserve placeholders like __KXH_0__ exactly.
Do not add explanations. Do not translate code, URLs, or IDs (they are already placeholdered).
Return translation only.

EN→JA system:
あなたはソフトウェア開発チャット向けの正確な英日翻訳者です。
ユーザーテキストのみ自然な日本語に翻訳してください。__KXH_0__等のプレースホルダは厳密に保持してください。
解説を付けず、翻訳文のみ返してください。コード・URL・IDは翻訳しないでください（既にプレースホルダ化済み）。
```

- `temperature=0.1` 固定で翻訳呼び出し（用語安定性優先）。将来的にRules画面で編集可能にするため `prompts.py` に定数分離する。

---

## 7. ストリーミング設計（P5の中核）

### 7.1 方針（設計書FR10の具体化）

- **Request側（JA→EN）は非ストリーミングで先行翻訳**: Ingressが `stream:true` でも、まず全メッセージをJA→EN翻訳してからEgressへ `stream:true` で転送する（逐次翻訳の複雑化を避ける）。
- **Response側（EN→JA）は文バッファ逐次翻訳**: Egress SSEを文区切り（`。．.!?!\n`）でバッファし、文完成ごとにEN→JA翻訳（フォールバックチェーン経由）してIngress SSEで送出する。文末未満の残りはストリーム終了時にフラッシュ翻訳する。
- 翻訳無効・日本語なし・全滅時は原文デルタを素通しする。

### 7.2 SSE形式差（送出側）

**OpenAI Ingressへの送出** (`text/event-stream`):

```
data: {"id":"chatcmpl-kxh-...","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"content":"..."},"finish_reason":null}]}
...
data: {"...choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

**Anthropic Ingressへの送出**:

```
event: message_start\ndata: {"type":"message_start","message":{"id":"msg_kxh_...","type":"message","role":"assistant","content":[],"model":"...","stop_reason":null}}\n\n
event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n
event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}\n\n
...
event: message_stop\ndata: {"type":"message_stop"}\n\n
```

### 7.3 受信側パース

- OpenAI Egress受信: `data:` 行のJSON `choices[0].delta.content` を連結。
- Anthropic Egress受信: `event:` + `data:` ペアを解釈し `content_block_delta(delta.text)` / `message_delta(delta.stop_reason)` を抽出。
- `tool_use` デルタ（`input_json_delta`）は翻訳せず素通し蓄積し、終了時に完全な `tool_use` として再送出する。

### 7.4 検証観点

- 空ストリーム・`[DONE]`欠落・tool_use混在・全滅パススルー・異種結合（OpenAI→Anthropic等）の4通りで破綻しないこと。

---

## 8. ロギング（`translate_log.py`）

- 配置: `~/.local/share/kakehashi/logs/translation.jsonl`（`~`展開）。1行1JSON（設計書§6.1の拡張フィールドをそのまま採用）。
- 追加フィールド: `model_override{requested,applied}`, `ingress_protocol/egress_protocol/egress_provider`, `translate_backend_used/translate_fallbacks/placeholder_fail`, `latency_ms{translate_in,upstream,translate_out,total}`, `stream`, `upstream_error?`。
- ローテーション: サイズ `translation_log_max_mb`（既定10MB）超過で `translation.jsonl.1〜.5` へ世代退避（自前実装、`logging.handlers.RotatingFileHandler`相当をJSONL用に簡易実装）。Web UIでON/OFF・上限変更可。
- 注意: 原文が平文で残る。Web UI Logging画面に注意文を明示し、即OFF可能にする。
- 通常アクセスログは `uvicorn` 既定に任せ、Kakehashi独自の構造化ログは翻訳ログのみとする（P0〜P6の簡素化）。

---

## 9. Web UI設計（`/ui` + `/api/config/*`）

### 9.1 配信方式

- `src/kakehashi/webui/static/{index.html,app.js,style.css}` をFastAPI `StaticFiles` で `/ui` にマウント。CDN不使用・完全自ホスト・ビルドレス（Vanilla JS + fetch）。
- `GET /ui` → `index.html`。`webui.enabled=false` 時は404。

### 9.2 画面構成（設計書§5.2通り）

- Dashboard / Providers(Egress) / Translation Backends / Translation Rules（予約表示） / Logging / Server。
- Providers画面がv3.0の肝: 一覧（名前・プロトコル・エンドポイント・選択モデル・Activeラジオ）＋登録/編集ウィザード＋モデル設定＋Test疎通。

### 9.3 REST API仕様（`webui/api.py`）

| Method/Path | 機能 | ボディ/応答 |
|---|---|---|
| `GET /api/config/full` | 設定全体取得（秘密はマスク `***`） | `AppConfig`相当JSON |
| `PUT /api/config/server` | Server更新 | `{host,port,api_key?}` |
| `GET /api/config/providers` | 一覧 | `[{id,name,protocol,base_url,model,active}]`（キー非表示） |
| `POST /api/config/providers` | 新規登録 | Step3相当 `{name,protocol,base_url,api_key?,api_key_env?,model,timeout_s,params}`→作成 |
| `PUT /api/config/providers/{id}` | 編集 | 同上（`id`変更不可） |
| `DELETE /api/config/providers/{id}` | 削除 | Active削除時は400（先に切替要求） |
| `POST /api/config/providers/active` | Active切替 | `{id}`→即時反映 |
| `POST /api/config/providers/{id}/test` | 疎通確認 | 上流へ `GET {base}/models`（10秒）or 最小chat送信。結果 `{ok, latency_ms, models?, error?}` |
| `POST /api/config/providers/fetch-models` | モデル一覧取得（保存前） | `{protocol,base_url,api_key?,api_key_env?}`→`{models:[id...], error?}`。失敗でも400にせず200＋error（手入力救済のため） |
| `GET /api/config/backends` / `POST` / `PUT /{id}` / `DELETE /{id}` / `POST /reorder` | 翻訳バックエンド管理 | D&D並替は `POST /reorder {ids:[...]}` |
| `POST /api/config/backends/{id}/test` | 翻訳疎通 | 固定文「こんにちは」JA→EN翻訳テスト |
| `GET/PUT /api/config/logging` | ログ設定 | `{translation_log_enabled,...}` |
| `GET /api/config/dashboard` | 24h統計 | `{requests, fallback_count, log_size_bytes}`（ログ走査で集計） |
| `POST /api/config/reload` | ディスク再読込 | 手動編集反映用 |

- 全 `/api/config/*`（GET含む）は `server.api_key` 設定時のみ `X-API-Key` 必須。SPAは `localStorage` にキーを保持し全fetchに付与する。
- バリデーション失敗は `422 {detail}`、存在なしは `404`。

### 9.4 プロバイダー登録フロー実装（FR13）

1. Step1入力→ 2. `[モデル一覧取得]` で `POST fetch-models` → 3. 成功時は `<select>` に充填、失敗時はエラー表示＋手入力 `<input>` を常時併置（救済必須）→ 4. `[Save]` で `POST/PUT providers`。
2. `fetch-models` 実装:

```
protocol=openai:    GET {base}/models + Authorization: Bearer → data[].id 列挙
protocol=anthropic: GET {base}/models + x-api-key/anthropic-version → data[].id 列挙
timeout=10秒固定。パース失敗・非実装（404/405等）は {models:[], error:"..."} で返却し登録ブロックしない
```

### 9.5 モデル設定UI（FR14）

- プロバイダー編集内に `temperature/max_tokens/top_p/stop/extra_body(JSON)/merge_policy` を配置。全任意・空欄は「ハーネス要求値尊重」。`extra_body` はJSONバリデーション付きtextarea。Anthropic選択時は `extra_body` を非表示＋注意書き。

---

## 10. ヘルス・エラー・認証

- `GET /healthz`: `{"status":"ok","version":"3.0.0"}`。
- `GET /healthz/upstream`: アクティブEgressへ `GET models`（10秒）。成功→`{"status":"ok","provider":"...","latency_ms":...}`、失敗→HTTP200＋`{"status":"degraded","error":"..."}`。
- 上流エラー透過: Egress 4xx/5xxは ingress形式に包んでそのまま返す（例: OpenAI ingressには `{"error":{"message":..., "type":...}}`＋元status）。
- 認証失敗: `401 {"error":"unauthorized"}`。詳細にキー値を漏らさない。

---

## 11. フェーズ別実装計画（P0〜P10）

### P0: 雛形・設定ロード・ヘルス — 完了条件 `/healthz` 200

- [ ] `pyproject.toml`、`src/kakehashi/{__init__,__main__,cli,app,config}.py` 作成
- [ ] `ConfigStore`＋既定config自動生成＋`600`保存
- [ ] `GET /healthz`、`GET /ui` スタブ、`--port`/`KAKEHASHI_PORT` 対応
- [ ] `pytest tests/test_config.py`（生成・再読込・権限・マスク）
- 検証: `python -m kakehashi serve & curl localhost:8090/healthz`

### P1: OpenAI Ingress→OpenAI Egress素通し — OpenAIクライアント往復成功

- [ ] `models_canonical.py`、`protocol_openai.py`（ingress→canonical→egress再展開）
- [ ] `egress.py`（openai送信、モデル上書き、`merge_policy`適用、`resolve_secret`）
- [ ] `routes/openai_ingress.py`（非streamのみ。stream要求は `400 stream not yet supported` で明示）
- [ ] `GET /v1/models` 素通しプロキシ
- 検証: `curl POST /v1/chat/completions` をCodeRouter/llama.cpp直結時と比較して往復成功。ログに `model_override` 記録確認

### P2: Anthropic Ingress→OpenAI Egress — Anthropicクライアント往復成功

- [ ] `protocol_anthropic.py`（ingress→canonical: system畳み・tool_use/tool_result・`max_tokens`補完）
- [ ] `canonical→anthropic response`（`stop_reason`写像・`system`戻し）
- [ ] `routes/anthropic_ingress.py`（非stream）
- [ ] `tests/test_convert_oai_anthropic.py`（system/tool/stop写像の往復単体テスト）
- 検証: Anthropic形式curlでOpenAI上流へ往復成功

### P3: Anthropic Egress（4通り完成） — 全組合せ往復成功

- [ ] `egress.py` にanthropic送信分岐（`x-api-key`/`anthropic-version`、`max_tokens`補完、`extra_body`無視警告）
- [ ] `canonical→egress` を `to_openai_payload` / `to_anthropic_payload` に分離
- [ ] `tests/test_e2e_protocols.py`（4通りマトリクスをモック上流で検証）
- 検証: Ingress{OA,AN}×Egress{OA,AN}の4通りcurl往復成功

### P4: 翻訳エンジン＋フォールバック — 保護・全滅パススルー確認

- [ ] `translate/{detector,protector,client,engine,prompts}.py`
- [ ] Ingress共通パイプラインに `translate_request/response` 組込＋ `translation.enabled` 分岐
- [ ] フォールバックチェーン＋cooldown＋全滅パススルー＋警告ログ
- [ ] `tests/test_protector.py/test_detector.py/test_fallback.py`（コード・URL・UUID混在、429→次点、全滅→原文）
- 検証: 日本語curl→上流に英語到達・応答日本語化をログ `original/translated` で目視確認

### P5: ストリーミング — SSE逐次翻訳が破綻しない

- [ ] Egress `httpx.stream` 受信→Canonicalデルタ正規化
- [ ] 文バッファ逐次翻訳（§7.1）＋tool_useデルタ素通し
- [ ] Ingress別SSE再送出（OA chunk / AN event）
- [ ] `stream:true` 400制限を解除
- 検証: `curl -N` で両Ingress×両EgressのSSE逐次受信・`[DONE]/message_stop`正常終端

### P6: 原文ローリングログ — ローテーション動作確認

- [ ] `translate_log.py`（JSONL追記・サイズローテ10MB×5・ON/OFF）
- [ ] パイプライン全経路（stream/非stream・4通り・全滅・上流エラー）で `request_id` 贯通記録
- [ ] `tests/test_logging.py`（ローテーション・フィールド存在）
- 検証: 上限を一時的に小値化して世代退避を確認

### P7: Web UI MVP（Providers） — GUI登録→即時反映

- [ ] `webui/api.py` のproviders系＋`fetch-models`＋`test`＋`active`切替
- [ ] `webui/static/` にProviders画面（ウィザード3Step・手入力救済・モデル設定・Test）
- [ ] 秘密マスク・`600`保存・再起動なし反映
- 検証: GUIでプロバイダ追加→直後curlが新プロバイダ経由になること。一覧失敗時の手入力登録も確認

### P8: Web UI残り — 全設定GUI変更→再起動なし反映

- [ ] Backends（D&D並替・Test）/ Rules（予約表示）/ Logging（ON/OFF・上限・最新エントリ）/ Server / Dashboard（24h統計）
- [ ] `GET /api/config/full|dashboard` 等
- 検証: 全画面の保存→`config.yaml`反映→次リクエストに適用されること

### P9: 常駐化・実機接続 — ハーネス設定をKakehashiのみにして運用成立

- [ ] `systemd/kakehashi.service` 配置・`systemctl --user enable --now`
- [ ] `0.0.0.0:8090` LAN公開・Kilo Codeのエンドポイントを `http://<host>:8090/v1` に切替（モデル名任意）
- [ ] 障害時切戻し手順書（READMEに1行変更手順を記載）
- 検証: Kilo Code実タスク往復成功

### P10: 1週間観測 — 安定判断

- [ ] `latency_ms` 分解・フォールバック率・`placeholder_fail`率の定期目視
- [ ] 未知フィールド警告ログからの変換表拡張
- 検証: 運用メモとして記録

---

## 12. テスト計画（設計書§10の実装割付）

| # | 観点 | 実装先 | 期待 |
|---|---|---|---|
| 1 | プロトコル4通り | `test_e2e_protocols.py`（モック上流）＋P3 curl | 全組合せ往復成功 |
| 2 | モデル上書き | `test_egress_params.py`＋実機curl | 存在しないモデル名→Egress選択モデル置換・ログ記録 |
| 3 | モデル一覧取得 | `fetch-models`単体＋GUI操作 | 一覧表示・非対応時は手入力誘導 |
| 4 | パラメータmerge | `test_egress_params.py` | override/client_wins両値検証 |
| 5 | 翻訳往復 | P4 curl＋ログ目視 | 意味保持・破綻なし |
| 6 | フォールバック・全滅 | `test_fallback.py` | 次点切替／パススルー＋ログ |
| 7 | コード保護 | `test_protector.py` | 非破壊復元 |
| 8 | ストリーミング | P5 `curl -N`＋E2E | 逐次翻訳・終端正常 |
| 9 | CodeRouter併用 | P9実機 | 動的切替が機能 |
| 10 | 即時反映 | P7/P8 GUI→curl | 再起動なし適用 |
| 11 | ローテーション | `test_logging.py` | 世代退避 |
| 12 | 復旧性 | README手順＋停止試験 | 直接接続切替が成立 |

実行コマンド（各Phase共通）:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"  # pytest 追加時は extras 検討
pytest -q
python -m kakehashi serve --port 8090
curl -s localhost:8090/healthz; curl -s localhost:8090/healthz/upstream
```

---

## 13. ビルド・運用メモ

- Python 3.12+ / venv / systemd常駐、Docker不要（NFR1）。
- 初回起動で雛形config生成→Web UI `/ui` でProviders登録→Active切替の順に運用開始する。
- LAN前提・既定 `0.0.0.0`。インターネット露出時はAPIキー有効化またはリバースプロキシ認証を追加すること。
- Kakehashi停止時はハーネスのエンドポイントを上流直結に戻す（1行変更）。

---

## 14. 未決・要注意事項（実装中に確定）

1. `GET /v1/models` の素通し要否はP1で最終判断する（ハーネス互換性が高ければ残す）。
2. Anthropic Egressの `stop_sequences` とOpenAI `stop` の相互変換は主要ケースのみ先行実装し、未知値は警告ログ＋運用拡張とする。
3. ストリーミング翻訳の文区切り正規表現は運用で調整する（初期値: `[。．！？!?\\n]`）。
4. `translation.rules[]` と `egress.chain[]` はパース許容・不使用・UI予約表示とし、実装は将来拡張に回す。

---

## 15. 決定事項サマリ（本計画書での確定）

| 項目 | 決定 |
|---|---|
| 言語/依存 | Python 3.12+ / fastapi, uvicorn, httpx, pyyaml, pydantic（jinja2不採用） |
| 中間表現 | Canonical dataclass経由の独立変換関数 |
| Egress冗長化 | なし（CodeRouter委譲、`chain`予約） |
| ストリーミング | Request先行翻訳＋Response文バッファ逐次翻訳 |
| ログ | 自前JSONLローテーション 10MB×5 |
| Web UI | 同一ポート `/ui` 静的SPA＋`/api/config/*` REST、秘密マスク・600保存 |
| 着手順序 | P0素通し→P2/P3変換→P4翻訳→P5ストリーミング→P6ログ→P7/P8 UI→P9常駐→P10観測 |
