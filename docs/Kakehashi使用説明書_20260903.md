# Kakehashi 使用説明書（初心者向け完全ガイド）

**対象バージョン**: 3.0.0
**作成日**: 2026-09-03
**想定読者**: プログラミング初心者〜中級者。LLM・API・Linuxに不慣れな方も順番に読めば使える構成。

---

## 目次

1. [はじめに](#1-はじめに)
2. [用語集](#2-用語集)
3. [Kakehashiの仕組み](#3-kakehashiの仕組み)
4. [必要なもの](#4-必要なもの)
5. [インストール](#5-インストール)
6. [起動・停止・常駐](#6-起動停止常駐)
7. [Web UIガイド](#7-web-uiガイド)
8. [ハーネス側の設定](#8-ハーネス側の設定)
9. [実際に使ってみる](#9-実際に使ってみる)
10. [設定ファイルリファレンス](#10-設定ファイルリファレンス)
11. [ログの見方](#11-ログの見方)
12. [トラブルシューティング](#12-トラブルシューティング)
13. [よくある質問FAQ](#13-よくある質問faq)
14. [セキュリティと注意点](#14-セキュリティと注意点)
15. [切戻し・アンインストール](#15-切戻しアンインストール)
16. [付録](#16-付録)

---

## 1. はじめに

### 1.1 Kakehashi（かけはし）とは

Kakehashiは**日英⇄英日 自動翻訳APIプロキシ**です。

難しく聞こえますが、やっていることは単純です。

> 日本語で書いた質問を英語に訳してAIに渡し、AIの英語回答を日本語に訳して返す「通訳者」

### 1.2 なぜ必要か

軽量・高速化されたローカルLLM（REAP枝刈りモデル等）は**英語の推論は得意だが日本語の生成が苦手**な場合があります。モデル自体を作り直すのは大変です。

Kakehashiはモデルの重みに触らず、**入出力だけを翻訳して迂回**します。

```
あなた（日本語） → Kakehashiが英語に翻訳 → AI（英語で推論） → Kakehashiが日本語に翻訳 → あなた（日本語）
```

### 1.3 できること / できないこと

| できること | できないこと |
|---|---|
| 日本語の質問を英語化して上流へ送信 | システムプロンプトの翻訳（対象外） |
| 英語の回答を日本語化して返却 | 道具呼び出し（tool_calls）の本体翻訳（構造は保持） |
| OpenAI形式・Anthropic形式の両対応（出入口で組合せ自由） | 完璧な翻訳（創作・文脈依存文は劣化し得る） |
| コード・URL・UUIDを壊さず保護 | 画像・音声の翻訳 |
| 翻訳失敗時の自動予備切替・原文素通し | 上流AI自体の冗長化（CodeRouterに委譲） |
| Webブラウザでの設定管理 | |

---

## 2. 用語集

初心者がつまずきやすい言葉をまとめました。分からなくなったらここに戻ってください。

| 用語 | ひと言 | 詳しい説明 |
|---|---|---|
| プロキシ | 代理人・中継役 | あなたとAIの間に立ってリクエストを中継するソフト。Kakehashi自体がプロキシです |
| ハーネス | AIを使う側の道具 | Kilo Code・Claude Code等のコーディング支援ツール。Kakehashiに質問を投げる「お客さん」 |
| Ingress | 入口 | ハーネス→Kakehashiの通信面。OpenAI形式（`/v1/chat/completions`）とAnthropic形式（`/v1/messages`）の2種を受け付けます |
| Egress | 出口 | Kakehashi→上流AIの通信面。こちらもOpenAI/Anthropicの2種に対応。上流の切替はWeb UIだけで完結します |
| OpenAI互換 | OpenAI社と同じ話し方 | `chat/completions` という住所で会話する形式。llama.cpp・LM Studio・Ollama・CodeRouterの多くがこの形式です |
| Anthropic互換 | Anthropic社と同じ話し方 | `messages` という住所で会話する形式。Claude系が使います |
| CodeRouter | 中継・切替器 | 複数のAI（プロバイダー/モデル）を束ねて切り替える常駐ソフト（既定 `127.0.0.1:8088`）。Kakehashiの後段に置きます |
| プロバイダー | AIの提供元 | CodeRouter・llama.cpp直接・Anthropic公式API等の「上流の宛先」。Web UIのProviders画面で登録します |
| モデル | AIの頭脳の種類 | `qwen3.8-reap384`・`claude-sonnet-4-5`等の名前。ハーネス側は任意名でよく、Kakehashiが上流の正しい名に上書きします |
| トークン | AIが読む最小単位 | 英単語の断片・日本語の1〜2文字等。`max_tokens` は回答の長さ上限です |
| temperature | ランダム度 | 0に近いと堅実・正確、1に近いと創造的・ばらつく。翻訳は0.1固定、通常応答はWeb UIで設定可 |
| SSE / ストリーミング | 逐次返信 | 回答全文を待たず少しずつ表示する方式。Kakehashiは文単位で翻訳しながら流します |
| プレースホルダ | 一時置き場の目印 | コード等を `__KXH_0__` に置換して翻訳破壊を防ぐ仕組み。翻訳後に元に戻します |
| フォールバック | 予備への切替 | 翻訳用AIが429/5xx/タイムアウトで失敗したら次の候補へ自動切替。全滅時は原文のまま通します |
| 429 / 5xx | 混雑・故障の合図 | 429=混雑（使いすぎ）、5xx=相手側故障。Kakehashiはこれらで予備へ切り替えます |
| ローリングログ | 自動整理される日誌 | `translation.jsonl` に翻訳前後ペアを保存。10MB×5世代で古い物から退避します |
| venv |隔離されたPython部屋 | システムを汚さず依存品を入れる仮想環境。`.venv` フォルダがそれです |
| systemd | 常駐番 | Linuxの常駐管理者。再起動後も自動起動させます |
| エンドポイント | 住所 | `http://host:8090/v1` のような接続先。ハーネスにはKakehashiの住所だけ教えます |
| APIキー | 合言葉 | 不正利用防止のパスワード。既定は不要（無効）。LAN公開時は設定推奨 |
| Base URL | 上流の住所 | プロバイダーの場所。例 `http://127.0.0.1:8088/v1`、`https://api.anthropic.com` |
| Active | 現在使用中 | 複数プロバイダーのうち実際に使う1件。ラジオボタンで切替、即時反映されます |

---

## 3. Kakehashiの仕組み

### 3.1 配置図

```
【構成A】CodeRouter併用（推奨・現行運用）
  あなた（Kilo Code等）
    → Kakehashi(8090) [日本語→英語に翻訳]
    → CodeRouter(8088) [どのAIに流すか決定]
    → ローカルLLM（英語で推論）
    → Kakehashi [英語→日本語に翻訳]
    → あなた（日本語で受取）

【構成B】CodeRouterなし（将来の標準）
  あなた → Kakehashi(8090) → ローカルLLM / クラウドAPI（直接）
```

> 順番は「Kakehashiが前・CodeRouterが後」で固定です。逆にするとCodeRouterの切替機能が働きません。

### 3.2 1リクエストの流れ

1. ハーネスが日本語で質問（OpenAI形式でもAnthropic形式でもOK）
2. Kakehashiが `user` の日本語だけ英語に翻訳（コード等は保護）
3. アクティブなプロバイダーへ転送（モデル名・温度等をWeb UI設定で上書き）
4. 上流の英語回答を日本語に翻訳
5. 元の形式（OpenAI/Anthropic）に戻して返却
6. 翻訳前後ペアをログに保存

### 3.3 ポート早見表

| 用途 | 既定値 | 変更方法 |
|---|---|---|
| Kakehashi本体+Web UI | `0.0.0.0:8090` | `KAKEHASHI_PORT` / config `server.port` |
| CodeRouter | `127.0.0.1:8088` | CodeRouter側の設定 |
| ヘルス | `/healthz`、`/healthz/upstream` | 固定 |

---

## 4. 必要なもの

- Linux PC（動作確認: Ubuntu 24.04）
- Python 3.12以上（`python3 --version` で確認）
- 上流のいずれか: CodeRouter（推奨）/ llama.cpp / LM Studio / Ollama / Anthropic API等
- ブラウザ（Web UI用、Chrome等）

---

## 5. インストール

### 5.1 初回セットアップ（コピー＆ペーストでOK）

```bash
cd /home/masaru/src/kakehashi
python3 -m venv .venv
./.venv/bin/pip install -e ".[test]"
./.venv/bin/python -m kakehashi validate
```

成功すれば `config OK: /home/masaru/.config/kakehashi/config.yaml` と出ます。初回は雛形configが自動生成されます。

### 5.2 動作確認（常駐化前のお試し起動）

```bash
./.venv/bin/python -m kakehashi serve --port 8090
# 別ターミナルで
curl -s http://127.0.0.1:8090/healthz
# {"status":"ok","version":"3.0.0"} と出ればOK。Ctrl+Cで停止
```

### 5.3 テスト（任意だが推奨）

```bash
./.venv/bin/python -m pytest tests -q
# 17 passed と出れば正常
```

---

## 6. 起動・停止・常駐

常駐化済みの場合、普段は意識不要です。再起動後も自動で立ち上がります。

| やりたいこと | コマンド |
|---|---|
| 状態確認 | `systemctl --user status kakehashi --no-pager` |
| 起動 | `systemctl --user start kakehashi` |
| 停止 | `systemctl --user stop kakehashi` |
| 再起動 | `systemctl --user restart kakehashi` |
| 自動起動の有効化 | `systemctl --user enable kakehashi` |
| 自動起動の無効化 | `systemctl --user disable kakehashi` |
| ログを live 表示 | `journalctl --user -u kakehashi -f` |
| ヘルス確認 | `curl -s http://127.0.0.1:8090/healthz` |
| 上流到達性確認 | `curl -s http://127.0.0.1:8090/healthz/upstream` |

> `Active: active (running)` と出ていれば常駐中です。`8090` が応答しない場合は§12を参照してください。

---

## 7. Web UIガイド

### 7.1 アクセス

ブラウザで開く:

```
http://127.0.0.1:8090/ui/
```

別PC（同一LAN）からは `http://<KakehashiのIP>:8090/ui/`。※ポート `:8090` の省略不可。

APIキーを設定している場合は右上の入力欄に入れて「保存」してください（`localStorage` に記憶されます）。このAPIキーはKakehashi本体の合言葉（`server.api_key`）であり、上流や翻訳用とは別物です。

外観: 見出しは虹色グラデーション、上部は濃紺・下部は薄水色。メニューは日本語表記（ダッシュボード／接続先LLM／翻訳モデル／翻訳指示／ログ／サーバー）です。

### 7.2 ダッシュボード（稼働状況）

グラフィカル表示（JSONではありません）。「更新」ボタンで再取得します。

- **状態カード**: 稼働件数（直近1000件）・上流到達性（OK/異常＋latency）・フォールバック件数/率・ログ量・翻訳有効無効を色分け表示
- **接続先LLM（Egress）**: 使用中の名前・ID・プロトコル・接続先URL・使用モデル
- **使用中の翻訳モデル（優先順）**: 全チェーンを#1から順に表示。有効無効・最終使用バッジ付き
- **レイテンシ平均・内訳**: 翻訳IN／上流／翻訳OUTの棒グラフ＋合計平均・入力形式内訳・ストリーミング件数・プレースホルダ失敗計
- **最近のリクエスト**: 直近10件の時刻・経路・モデル・翻訳backend・処理時間

### 7.3 接続先LLM（旧Providers・Egress・上流管理）★最重要画面

**一覧の見方**: `ID`・名前・プロトコル・接続先URL・モデル・使用中表示。

#### 新規登録の手順（3ステップ）

1. **基本情報を入力**
   - `名前`: 自分が分かる名前（例 `my-local-server`）
   - `プロトコル`: `OpenAI互換` か `Anthropic` を選択
   - `接続先URL`: 上流の住所（例 `http://127.0.0.1:8088/v1`。末尾 `/chat`・`/chat/completions`・`/messages` 付きでも自動正規化されます）
   - `APIキー環境変数名`（推奨）または `APIキー直接`: 秘密は設定ファイル600権限で保護・マスク表示
2. **［モデル一覧取得］を押す**
   - 成功 → ドロップダウンに実在モデルが並ぶので選択
   - 失敗 → エラー表示＋手入力欄に直接書く（救済措置。llama.cpp等の `/models` 未実装サーバで発生。**失敗は想定内**です）
3. **モデル設定（任意）→［保存］**
   - `temperature` / `max_tokens` 等は空欄でOK（空=ハーネスの要求を尊重）
   - 保存直後から有効（再起動不要）

#### よく使う操作

- **使用中に設定**: 使いたい行の「使用中に設定」→直後のリクエストから切替
- **接続確認**: `GET {base}/models` で疎通確認（10秒）。結果に `ok/latency_ms/models/error` 表示
- **削除**: 使用中は削除不可（先に他へ切替）
- **編集**: 同じIDで保存（上書き）。`merge_policy` は `override`（Kakehashi優先・既定）か `client_wins`（ハーネス優先）

### 7.4 翻訳モデル（旧Translation Backends・翻訳用AI管理）

接続先LLMと同じ3ステップ登録フローです（基本情報→［モデル一覧取得］→選択／手入力→保存）。

- 一覧: ID・名前・プロトコル・接続先URL・モデル・有効／無効。「編集」「接続確認」「削除」付き
- 新規はID空で自動採番、編集はIDを指定して上書き保存
- 優先順位の上から順に試行します（登録順・reorder APIで並替可）
- `接続確認` は固定文「こんにちは」でJA→ENを試します
- 全部失敗しても本線は止まりません（原文素通し＋警告ログ）

### 7.5 翻訳指示（プロンプト管理）

翻訳モデルに送信するシステムプロンプトを日英（JA→EN）・英日（EN→JA）別に編集できます。

- 空保存は無視されます。`__KXH_0__` 等のプレースホルダ保持指示は残してください
- ［保存］で次リクエストから即時反映（再起動不要）、［既定に戻す］で初期文に復元

#### 出力言語ガード

上流の生成言語を縛る指示（既定: 英語のみ・中国語禁止）。空で無効化できます。中国製モデル等の母語混入抑止に使用します。

#### コード内表示文字列の翻訳

道具呼び出し（ファイル生成等）の引数に含まれる表示文字列（print文・help・エラー文・docstring等）を英日翻訳します。

- チェックON/OFFと最小文字数（既定8）を設定可。保存で即時反映
- Pythonソース対応。AST解析で文字列リテラルだけをexact置換し、再解析検証に失敗した場合は原文のまま返す安全設計（単行・単行triple引用・f-string内定数部・複数行docstringに対応。曖昧な箇所は個別除外）
- 識別子・SQL・URL・パス・`{...}` 形式欄は翻訳対象外。日本語由来の文字列も対象外
- 複数リテラルは1回の翻訳呼出に束ねて処理（-marker不一致時は個別再試行）
- ログの `code_strings` 件数で翻訳数を、中身は `translation.jsonl` で確認できます

### 7.6 ログ（旧Logging・原文ログ管理）

- 「記録する」のON/OFF・上限・世代数・最新エントリ表示。
- **原文・訳文が平文で残ります**。外部共有前に中身を確認し、不要時は即OFFにできます。

### 7.7 サーバー（旧Server）

- `ホスト` / `ポート番号` / `APIキー` の変更。`APIキー` 空=認証なし（既定）。
- ポート変更後は常駐再起動が必要です: `systemctl --user restart kakehashi`。

---

## 8. ハーネス側の設定

Kilo Code等には**Kakehashiだけ**を設定します。上流を変えてもハーネスの再設定は不要です。

| 項目 | 値（例） |
|---|---|
| APIエンドポイント（Base URL） | `http://127.0.0.1:8090/v1`（別PCならIPを置換） |
| APIキー | Kakehashiで有効化時のみ入力（既定は空でOK） |
| モデル名 | **任意の文字列でOK** |

> なぜモデル名が任意でよいのか: Kakehashiが転送時にアクティブプロバイダーの選択モデルで**必ず上書き**するからです（`model_override` としてログ記録）。ただしCodeRouterに `model: auto` で委ねる運用では、CodeRouterが知る正しいモデル名を入れてください。

---

## 9. 実際に使ってみる

### 9.1 最小確認（curl）

```bash
# OpenAI形式
curl -s http://127.0.0.1:8090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"anything","messages":[{"role":"user","content":"この関数をリファクタリングして"}]}' | head -c 800
echo

# Anthropic形式
curl -s http://127.0.0.1:8090/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"anything","max_tokens":200,"messages":[{"role":"user","content":"この関数を説明して"}]}' | head -c 800
echo

# ストリーミング（逐次表示）
curl -N http://127.0.0.1:8090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"anything","stream":true,"messages":[{"role":"user","content":"箇条書きで3行説明して"}]}'
```

### 9.2 ログで翻訳を確認

```bash
tail -n 3 ~/.local/share/kakehashi/logs/translation.jsonl | python3 -m json.tool
```

`request_original`（日本語）→ `request_translated`（英訳後）、`response_upstream`（翻訳前の英語応答）→ `response_final`（日本語訳後）の4点、`model_override`、`translate_backend_used`、`latency_ms` が見えます。Web UIのログ画面でも最新エントリを確認できます。

---

## 10. 設定ファイルリファレンス

場所: `~/.config/kakehashi/config.yaml`（`KAKEHASHI_CONFIG` で変更可、権限600、手動編集可→ `/api/config/reload` か常駐再起動で反映）。

```yaml
server:
  host: "0.0.0.0"   # LAN公開。自分のみなら 127.0.0.1
  port: 8090
  api_key: ""       # 空=認証なし

egress:
  active_provider: "coderouter"
  providers:
    - id: "coderouter"
      name: "CodeRouter (local)"
      protocol: "openai"              # openai | anthropic
      base_url: "http://127.0.0.1:8088/v1"
      api_key_env: ""                 # 環境変数名を推奨
      model: "auto"                   # 実ID or auto（CodeRouter委譲）
      timeout_s: 300
      params:
        merge_policy: "override"      # override | client_wins
        # temperature: 0.2
        # max_tokens: 8192
        extra_body: {}                # OpenAI互換のみ有効

translation:
  enabled: true
  cjk_threshold: 0.1                  # 日本語判定閾値（小=敏感）
  protect_patterns: [code_block, inline_code, url, uuid, path]
  backends:
    - id: "tb-local"
      protocol: "openai"              # openai | anthropic（一覧取得用。翻訳呼出はOpenAI互換）
      base_url: "http://127.0.0.1:1234/v1"
      model: "translategemma-12b"
      timeout_s: 30
      enabled: true
  prompts:                            # 翻訳指示タブで編集可
    ja2en: "..."                      # 日英システムプロンプト
    en2ja: "..."                      # 英日システムプロンプト
    output_guard: "..."               # 上流への出力言語ガード（空=無効）
  code_strings:                       # 道具引数内の表示文字列翻訳
    enabled: true
    min_length: 8
  retry:
    on_status: [429, 500, 502, 503, 504]
    on_timeout: true
    cooldown_s: 60
  rules: []                           # 将来拡張用

logging:
  translation_log_enabled: true
  translation_log_dir: "~/.local/share/kakehashi/logs"
  translation_log_max_mb: 10
  translation_log_backups: 5
```

---

## 11. ログの見方

### 11.1 翻訳ログ（主役）

`~/.local/share/kakehashi/logs/translation.jsonl`（1行1JSON）:

| フィールド | 意味 |
|---|---|
| `ts/request_id` | 時刻・追跡ID |
| `ingress_protocol/egress_protocol` | 出入口の形式 |
| `egress_provider` | 使用した上流 |
| `model_override` | `{requested, applied}` の置換記録 |
| `translate_backend_used/fallbacks` | 翻訳に使った裏方・予備切替回数 |
| `placeholder_fail` | 0が正常。1以上は保護復元ミス（要目視） |
| `request_original/request_translated` | 日本語原文→英訳後（平文注意） |
| `response_upstream/response_final` | 翻訳前の英語応答→日本語訳後（平文注意） |
| `latency_ms` | `translate_in/upstream/translate_out/total` の内訳 |
| `stream` | ストリーミングか |
| `upstream_error` | 上流失敗時のみ |

### 11.2 常駐ログ

```bash
journalctl --user -u kakehashi -f
journalctl --user -u kakehashi --since "1 hour ago" | tail -n 100
```

---

## 12. トラブルシューティング

### Q1. `8090` に繋がらない

```bash
systemctl --user status kakehashi --no-pager
curl -s http://127.0.0.1:8090/healthz
ss -ltn | grep 8090
```

- `inactive` → `systemctl --user start kakehashi`
- ポート競合 → `server.port` を変更→ `restart`
- 設定破損 → `./.venv/bin/python -m kakehashi validate` で確認

### Q2. `/healthz/upstream` が degraded

- プロバイダーの `base_url` 誤り（`/v1` の有無）→ Providers画面のTestで確認
- CodeRouter停止 → CodeRouterを起動
- 認証失敗 → `api_key_env` の環境変数が常駐プロセスに見えているか確認（`systemctl --user edit kakehashi` で `Environment=` 追加が確実）

### Q3. モデル一覧取得が失敗する

**想定内です**。llama.cpp・非公式互換サーバは `/models` 未実装があり得ます。手入力欄に正しいモデルIDを書いて保存してください（FR13の救済フロー）。

### Q4. 日本語が翻訳されず素通しする

- `translation.enabled=false` → trueに
- 英語判定 → `cjk_threshold` を下げる（0.05等）
- 翻訳裏方全滅 → BackendsのTest・`fallback_count`・常駐ログの警告を確認
- `system` / `tool` は仕様上翻訳対象外（`user` のみ）

### Q5. コードが壊れる

- `protect_patterns` に `code_block` 等が含まれるか確認
- `placeholder_fail > 0` のログを目視し、該当パターンを報告
- 応急処置: 該当文をコードブロック（```）で囲む

### Q6. 401 unauthorized

- Web UI右上のキーと `server.api_key` の不一致。空運用に戻す場合はServer画面で空保存。

### Q7. 遅い

- 仕様上、翻訳2回分だけ遅くなります。`latency_ms` で `translate_in / upstream / translate_out` を切り分け。裏方の `timeout_s`・順序・ローカル翻訳モデルの使用を検討。

### Q8. 上流エラーが返る（4xx/5xx）

Kakehashiは上流エラーを握りつぶさず透過します。ログの `upstream_error` と常駐ログを確認し、上流（CodeRouter/LLM）側を修正してください。

---

## 13. よくある質問FAQ

**Q. ハーネスのモデル名は何を入れれば？**
A. 原則なんでもOK（上書きされるため）。`auto` 委譲運用時のみCodeRouterの正しい名を入れてください。

**Q. OpenAI形式とAnthropic形式の違いは意識すべき？**
A. いいえ。入口と出口は独立で4通り自動変換されます。ハーネスと上流で形式が違っても透過します。

**Q. Egressの冗長化（予備上流）は？**
A. v3.0では非搭載です。CodeRouterを後段に置いて委譲してください（`egress.chain` は将来予約）。

**Q. 原文ログを消したい/止めたい**
A. Logging画面でOFF→既存ファイルは `rm ~/.local/share/kakehashi/logs/translation.jsonl*` で削除できます。

**Q. インターネットに公開してよい？**
A. 非推奨（LAN前提）。公開時はAPIキー必須＋リバースプロキシ認証を検討してください。

---

## 14. セキュリティと注意点

- Listen既定 `0.0.0.0`（LAN公開）。不要な露出は避ける
- `config.yaml` は600権限・APIキーはマスク表示・再入力式
- 原文ログは平文。共有前に必ず目視
- Kakehashi停止時は系全体が止まるため、切戻し手順（次節）を控えておく

---

## 15. 切戻し・アンインストール

### 15.1 障害時切戻し（1行変更）

ハーネスのエンドポイントを上流直結に戻すだけです:

```
http://127.0.0.1:8090/v1  →  http://127.0.0.1:8088/v1（CodeRouter直結例）
```

### 15.2 自動起動の停止・常駐解除

```bash
systemctl --user stop kakehashi
systemctl --user disable kakehashi
# 完全削除時
rm ~/.config/systemd/user/kakehashi.service
systemctl --user daemon-reload
```

---

## 16. 付録

### A. エンドポイント一覧

| 用途 | Method/Path |
|---|---|
| OpenAI入口 | `POST /v1/chat/completions`（`/chat/completions` 別名あり） |
| Anthropic入口 | `POST /v1/messages` |
| モデル列挙素通し | `GET /v1/models` |
| ヘルス | `GET /healthz` / `GET /healthz/upstream` |
| Web UI | `GET /ui/` |
| 設定API | `/api/config/full・server・providers・providers/active・providers/fetch-models・providers/{id}/test・backends・backends/fetch-models・backends/reorder・backends/{id}/test・prompts・prompts/reset・logging・logging/tail・dashboard・reload` |

### B. コマンドチートシート

```bash
systemctl --user status kakehashi --no-pager
systemctl --user restart kakehashi
journalctl --user -u kakehashi -f
curl -s http://127.0.0.1:8090/healthz
curl -s http://127.0.0.1:8090/healthz/upstream | python3 -m json.tool
tail -n 5 ~/.local/share/kakehashi/logs/translation.jsonl
./.venv/bin/python -m kakehashi validate
./.venv/bin/python -m pytest tests -q
```

### C. 関連文書

- 全体設計書: `docs/Kakehashi｜日英⇄英日 自動翻訳APIプロキシ 全体設計書_20260903.md`
- 詳細実装計画書: `docs/Kakehashi詳細実装計画書_20260903.md`
- 本書: `docs/Kakehashi使用説明書_20260903.md`
