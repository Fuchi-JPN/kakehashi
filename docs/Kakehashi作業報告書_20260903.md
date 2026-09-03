# Kakehashi 作業報告書_20260903

**作業日**: 2026-09-03
**対象**: Kakehashi v3.0.0（日英⇄英日 自動翻訳APIプロキシ）
**成果物**: 実装一式・常駐化・使用説明書・本報告書
**最終状態**: テスト 35 passed、常駐 `active (running)`

---

## 目次

1. [概要](#1-概要)
2. [実装作業（P0〜P9）](#2-実装作業p0p9)
3. [常駐化](#3-常駐化)
4. [使用説明書の作成](#4-使用説明書の作成)
5. [デバッグ・改善履歴](#5-デバッグ改善履歴)
6. [最終状態](#6-最終状態)
7. [残課題・今後の拡張](#7-残課題今後の拡張)

---

## 1. 概要

全体設計書 v3.0 および詳細実装計画書に基づき、Kakehashi本体の実装・常駐化・ドキュメント整備を行った。以降の運用中に発生した障害・要望へはログ分析を起点に対応し、計23件のデバッグ・改善を実施した。

| 項目 | 内容 |
|---|---|
| 言語/依存 | Python 3.12 / fastapi, uvicorn, httpx, pyyaml, pydantic |
| Ingress | OpenAI互換 `/v1/chat/completions` ＋ Anthropic互換 `/v1/messages` |
| Egress | OpenAI互換／Anthropic互換の双方をWeb UIで登録・選択 |
| ポート | Kakehashi `0.0.0.0:8090` → CodeRouter `127.0.0.1:8088` → ローカルLLM |
| リポジトリ | `/home/masaru/src/kakehashi` |

---

## 2. 実装作業（P0〜P9）

### P0: 雛形・設定・ヘルス・CLI基盤

- `pyproject.toml`、`src/kakehashi/{__init__,__main__,cli,app,config}.py` を作成
- `ConfigStore`（既定config自動生成・600保存・RLock保護・ディスク再読込）を実装
- `GET /healthz`、`--port`／`KAKEHASHI_PORT` 対応、`validate`／`version` コマンド

### P1〜P3: Canonical変換＋Ingress/Egress 4通り

- `models_canonical.py`（Message／TextBlock／ToolUseBlock／ToolResultBlock／stop_reason正規化）
- `protocol_openai.py`（ingress→canonical→egress再展開、`developer`→`system`畳み、未知contentの保持）
- `protocol_anthropic.py`（`system`畳み・`tool_use`／`tool_result`保持・`max_tokens`既定4000補完）
- `egress.py`（アクティブ解決・モデル上書き・`merge_policy`・認証ヘッダ・`fetch_models`・`check_upstream`）
- `routes/{openai_ingress,anthropic_ingress,models_proxy}.py`（非stream往復・`/v1/models`素通し・`/healthz/upstream` degraded応答）
- E2Eマトリクステストで4通り往復を確認

### P4〜P6: 翻訳エンジン＋ストリーミング＋ログ

- `translate/{detector,protector,client,engine,prompts}.py`
  - CJK比率による日本語判定（既定閾値0.1）
  - プレースホルダ保護5種（code_block/inline_code/url/uuid/path）
  - 優先順位フォールバックチェーン＋cooldown＋全滅パススルー
  - JA→EN／EN→JAプロンプト定数
- ストリーミングは Request先行翻訳＋Response文バッファ逐次翻訳、tool_useデルタ素通し（当初）
- `translate_log.py`（JSONL追記・10MB×5世代ローテ・ON/OFF）

### P7〜P8: Web UI

- `/ui` 静的SPA（Vanilla JS＋fetch、完全自ホスト）＋`/api/config/*` REST
- Providers（登録3ステップ・モデル一覧取得・手入力救済・Test・Active切替・モデル設定）
- Backends／Logging／Server／Dashboard

### P9: 運用ファイル・テスト・検証

- `systemd/kakehashi.service`、`README.md`（切戻し手順含む）
- テスト8ファイル17件、全件通過を確認

---

## 3. 常駐化

1. `.venv` をプロジェクト内に新規作成し `pip install -e ".[test]"`（17 passed）
2. `systemd/kakehashi.service` のExecStartパス誤り（`%h/kakehashi`）を `%h/src/kakehashi` に修正、`WorkingDirectory` 追加、`Restart=always` 化
3. `~/.config/systemd/user/kakehashi.service` に配置し `daemon-reload`＋`enable --now`
4. `/healthz` ok・`/healthz/upstream` ok（CodeRouter実リスト取得）・`/ui/` 200を確認

---

## 4. 使用説明書の作成

`docs/Kakehashi使用説明書_20260903.md`（初版514行）を新規作成。用語集22項目・インストール手順・常駐コマンド・Web UIガイド・ハーネス設定・curl例・設定リファレンス・トラブルシューティング8問・FAQ5問・切戻し手順・付録を含む。以降のUI変更は§5の都度反映した（D9〜D14、D21、D23）。

---

## 5. デバッグ・改善履歴

### D1: Tailscale経由のUI接続拒否

- 症状: `http://100.77.176.99/ui/` で `ERR_CONNECTION_REFUSED`
- 分析: 常駐は `active`、`0.0.0.0:8090` 待受、ローカル到達OK、tailscale0に `100.77.176.99` 存在。URLにポート `:8090` が欠落していた
- 対応: 正URL `http://100.77.176.99:8090/ui/` を案内。`curl` で200を確認

### D2: Dashboardの内部エラー

- 症状: `{"error": {"message": "internal error"}}`
- 原因: `/api/config/dashboard` が `cfg.translation_log_dir` を参照（正しくは `cfg.logging.translation_log_dir`）し `AttributeError`
- 修正: `webui/api.py` を `cfg.logging` 経由に修正。再起動後に正常応答を確認

### D3: Translation Backendsの登録フロー統一＋さくらAIの404

- 要望: BackendsにもProvidersと同一の登録フロー（基本情報→モデル一覧取得→選択／手入力→保存）
- 対応1: `TranslateBackend` に `protocol` 追加（既存設定はopenai補完）、`POST /api/config/backends/fetch-models` 新設、一覧にprotocol返却、UIを3ステップ化（編集・Test・削除付き）
- 対応2: さくらAI `https://api.ai.sakura.ad.jp/v1/chat/` で一覧取得404。`fetch_models` と翻訳呼出・`egress_url` のURL正規化が `/chat` 接尾辞未対応だったため、`/chat`・`/chat/completions`・`/messages` を剥がす共通処理に修正。正Base URL（`/v1`）も案内

### D4: 管理UI2行目のAPIキーとは（回答のみ）

- 回答: Kakehashi本体の合言葉（`server.api_key`）。上流・翻訳用とは別物。既定空＝認証なし、設定時はIngressと `/api/config/*` の双方で同一値必須

### D5: 現在設定の一覧表示（回答のみ）

- `~/.config/kakehashi/config.yaml` を読取り、秘密値マスクで提示。Egress 1件・翻訳裏方2件（さくらAI／NVIDIA）・ログ等の構成を確認

### D6: 日英往復の疎通確認（正常）

- 日本語プロンプトを投入し、応答日本語化・ログ（`translate_backend_used: tb-8d084e21`、latency内訳 `6112/6974/3885ms`）で全段階完走を確認

### D7: 翻訳前後文のログ欠落

- 質問: 英訳プロンプトと翻訳前レスポンスは保存されているか
- 分析: メタデータのみで `original/translated` が欠落（設計§6.1未達）
- 修正: 両Ingressの非stream／streamに `request_original`・`request_translated`・`response_upstream`・`response_final` を追加。実ログで4点記録を確認

### D8: ログ配置の回答

- `~/.local/share/kakehashi/logs/translation.jsonl`（10MB×5世代）、Web UIのログ画面でも確認可

### D9: ダッシュボードの拡充・グラフィカル化

- API拡充: 状態・接続先LLM詳細・翻訳チェーン順位・レイテンシ平均・Ingress/Egress内訳・stream件数・直近10件・上流疎通
- UI: 状態カード（色分け）・接続先表・チェーン順位（最終使用バッジ）・レイテンシ棒グラフ・最近表に変更

### D10: 翻訳指示プロンプト編集タブ

- `translation.prompts{ja2en,en2ja}` を設定化し、翻訳実行は設定値を参照（空時は内蔵既定）
- API `GET/PUT /api/config/prompts`・`POST /prompts/reset`、UIにPromptsタブ（日英・英日＋保存／既定復元／再読込）。テスト署名不整合1件を修正

### D11: 見出しの虹グラデーション

- `header h1` に虹 `linear-gradient`＋`background-clip:text` を適用
- 追 repair: 短文ではブロック全幅に引き伸ばされ赤→橙しか掛からないため `display:inline-block` で文字幅に収めた

### D12: UIの日本語化

- メニュー・見出し・ボタン（Test→接続確認、Active化→使用中に設定等）・入力欄・動的一覧を日本語化。固有名詞・単位・値（OpenAI互換等）は残置

### D13: 配色変更

- 上部 `#0a1a4a` 濃紺、下部 `#e3f2fd` 薄水色（画面下端まで継続）

### D14: 使用説明書への反映

- D3・D7・D9〜D13の変更を§7・§9・§10・§11・§16に反映（メニュー名・ボタン名・ログ4フィールド・新API・配色注記等）。ついでに誤字1件修正

### D15: 「入力テキストが空のため」エラー

- 症状: Kilo Codeへの応答が `（入力テキストが空のため、翻訳する内容がありません）`
- 分析（request_id `a7b8145507c8`）: 上流が空応答 `"\n\n"` → ストリーミングの文フラッシュが空白文を翻訳裏方へ送信 → 裏方（Kimi）が空文への断り文句を返し、それがassistant応答に流出
- 修正: `translate_text` 中央と `translate_response` に空白のみ入力の抑止（裏方を呼ばず原文返却）。回帰テスト追加

### D16: 再投入後の無応答停止（タイムアウト帰属の特定）

- 症状: 回答なしに停止。直前リクエスト `total: 300000`
- 分析: Kakehashiのegressタイムアウト（300秒）は転送開始から計時のため発火すればtotal約353秒になるはず。きっかり300秒・サーバ側例外なし・POST完了記録なし → **Kilo Code側の約5分タイムアウトによる切断**が直接の引き金。上流LM Studioは無応答のまま。Kakehashiは被害者
- 対策提示: プロンプト縮小・裏方健全化・SSEキープアライブ（D17へ）

### D17: SSEキープアライブ実装

- 両Ingressのstreamingを改修: 翻訳INをジェネレータ内に遅延実行し、待機中 `: ping waiting translate_in` 送出。上流SSEはキューポンプ経由で受信し、15秒無音ごとに `: ping waiting upstream` 送出（`KEEPALIVE_S = 15`）。切断時は翻訳・ポンプ両タスクをキャンセル

### D18: 依然停止→真因は `reasoning_content` 破棄

- 分析: 転送ペイロード記録を追加し再投入→構造は正常（system 11KB＋user 2.7KB・tools有）。同一内容の上流直送は正常応答。差分は会話履歴・tools。上流SSEの直接観測で、上流Qwenが思考を `delta.reasoning_content` に流すことを確認。Kakehashiは `content` のみ読み破棄していたため、思考中は無音、思考のみ終端回は完全無応答
- 修正: 両デルタを抽出（Anthropic Egressのthinking_delta含む）。思考は無翻訳で即時転送（OpenAI Ingressには `reasoning_content` チャンク透過、Anthropic Ingressにはプレーンテキスト）。本文は従来通り翻訳。ログに `reasoning_chars` 追加。実機で思考透過を確認

### D19: 「Let me write the file.」で停止（tool_calls破棄）

- 分析: ファイル生成は上流の `tool_calls` デルタで運ばれるが、ストリーミング抽出が未実装（設計§7.3の素通し再送出が未実装）で破棄→Kilo Codeは道具呼び出しを受け取れず停止
- 修正: OpenAI形式断片・Anthropic形式tool_use／input_json_deltaを蓄積し、終端時に完全形で再送出（OpenAI側はtool_calls＋`finish_reason: tool_calls`、Anthropic側はtool_useブロック列＋`stop_reason: tool_use`）。ログに `tool_calls` 件数。実機で転送＋終端を確認

### D20: 生成後の推論無限ループ（道具結果の破損）

- 分析: 同一道具呼び出し反復（tc=1）・本文空・思考増大の進行は「道具結果がモデルに届かず再試行」の典型。`role: tool` の配列contentをPython `str()` で変換していたため `[{'type': ...}]` という壊れた引用符文が上流へ届いていた
- 修正: テキスト抽出＋未知部JSON保持に変更。入口構造の記録（役割列・content種別・tool_calls数）も追加。配列tool結果の正常保持を検証

### D21: 中国語注釈の混入（出力言語ガード）

- 症状: 生成コードの注釈がほぼ中国語（Qwen起因）
- 対応: プロンプト段階の介入として `translation.prompts.output_guard`（既定: 英語のみ・中国語禁止）を転送直前にシステム末尾へ付加。API・UI（翻訳指示タブ）・ログ記録付き。実ペイロードで付加を確認

### D22: 英語コードの未翻訳（仕様通り・無変更）

- 回答: ファイル内容は道具引数であり設計§3.3の対象外。翻訳すれば識別子・SQL等が破壊されるため英語のままが正規。注釈日本語化の選択肢（指示追記／ガード変更）を提示

### D23: コード内表示文字列の翻訳機能

- 要望: 注釈ほか画面表示・出力文の引数も英日翻訳対象に
- 実装（`translate/code_strings.py` 新設、既定ON・UI切替付き）:
  - PythonソースはAST解析で文字列リテラルだけをexact置換＋再解析検証（失敗時は原文）。`{...}` 形式欄保護、日本語由来・SQL・識別子・パスは除外
  - 非Pythonは厳密フィルタ通過分のみ全体翻訳。裏方全滅時は原文
  - 複数リテラルは `[KXH-i]` マーカーで1呼出に集約（不一致時は個別再試行）
  - 応答側の道具引数に適用（非stream 2経路＋stream終端2経路）。ログに `code_strings` 件数
  - API `GET/PUT /api/config/code-strings`、翻訳指示タブにON/OFF・最小文字数
- 検証: 35 passed。実機で `print("No transactions found.")` → `print("トランザクションが見つかりませんでした")` を確認（注釈・識別子・パスは維持）

---

## 6. 最終状態

### テスト

- 35 passed（内訳: 既存回帰＋empty guard 2・reasoning 4・tool stream 2・output guard 4・code strings 6 ほか）

### 常駐

- `kakehashi.service`: `active (running)`、`enabled`
- `/healthz` ok、`/ui/` 200

### 主要ファイル

```
src/kakehashi/{app,cli,config,egress,models_canonical,
  protocol_openai,protocol_anthropic,translate_log}.py
src/kakehashi/routes/{pipeline,openai_ingress,anthropic_ingress,models_proxy}.py
src/kakehashi/translate/{detector,protector,client,engine,prompts,code_strings}.py
src/kakehashi/webui/{api.py,static/index.html,app.js,style.css}
tests/test_{config,detector,protector,convert_oai_anthropic,egress_params,fallback,
  logging,e2e_protocols,empty_guard,reasoning,tool_stream,output_guard,code_strings}.py
docs/{全体設計書,詳細実装計画書,使用説明書,本報告書}
```

### 現在設定（秘密値除く）

- Server `0.0.0.0:8090`（認証なし）、Egress Active 1件（LM Studio系・openai互換）
- 翻訳裏方2件（さくらAI／NVIDIA、不安定時間帯あり）、プロンプト3種・ガード有効・code_strings有効
- ログ `~/.local/share/kakehashi/logs/translation.jsonl`（10MB×5世代、4フィールド拡張済み）

---

## 7. 残課題・今後の拡張

| 項目 | 内容 |
|---|---|
| 翻訳裏方の安定化 | さくらAI timeout・NVIDIA 503多発。ローカル翻訳モデル・timeout短縮・順序見直しの検討 |
| 巨大プロンプト対策 | spec全文添付時の翻訳IN 30〜50秒が律速。APIキーenv化・裏方健全化で短縮 |
| 上流無応答の早期検出 | 巨大日本語プロンプトでLM Studioが空応答・300秒停滞する事例あり。上流直叩き切分け手順の定着 |
| code_strings拡張 | Python以外（JS/TS/Go等）のリテラル対応、f-string内定数部の精密置換 |
| Egress冗長化 | v3.0方針通りCodeRouter委譲中。`egress.chain` は予約 |
| ルールベース詳細 | `translation.rules` 予約。用語集・正規表現置換は未実装 |
