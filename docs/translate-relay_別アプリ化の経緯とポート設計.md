# 翻訳Proxy別アプリ化の経緯とポート設計

**作成日**: 2026-09-03
**関連文書**: `webui/docs/coderouter-plugin-translate_企画書仕様書.md`（企画書兼仕様書）
**検証対象**: `zephel01/CodeRouter` v2.15.0 (`65ae720`)、 `zephel01/coderouter-plugin-compress` v0.2.1
**結論**: 翻訳機能はCodeRouterのプラグインではなく、**CodeRouterとは別の常駐アプリ（Standalone Relay方式＝企画書7節Phase 1b）として開発する**。本書はその判断に至った検証経緯と、CodeRouterとポート競合しないための設計示唆を残す。

---

## 1. 結論（先に要点）

| 項目 | 結論 |
|---|---|
| 実装方式 | CodeRouter無改造の別プロセス `translate-relay`（企画書7節Phase 1b） |
| Plugin方式（Phase 1a） | 不採用。入力側のみ可能で、応答側フックが存在しないためFR2を満たせない |
| CodeRouter側の変更 | 不要（`providers.yaml` に翻訳専用プロファイルを追加するのみ） |
| ポート | CodeRouter (`8088`)・WebUI (`8089`) と重ならない専用ポート（例: `8090`、環境変数で可変）をRelayに割り当て |

---

## 2. 別アプリ化に至った経緯

### 2.1 出発点：Plugin方式への期待

企画書は「CodeRouterの中核機能＝複数プロバイダ登録＋自動フォールバック」を流用し、`coderouter-plugin-compress` と同じ `InputFilter` パターンで翻訳プラグイン `coderouter-plugin-translate` を作る方針（Phase 1a）を第一候補とした。根拠は以下4点だった（企画書2.3節）：

1. フォールバック機構が成熟済みで再実装不要
2. プラグイン追加の前例（`compress`、`agents`）が実在
3. Pythonでスタック一致
4. 既にCOSTRA基盤として運用中

ただし企画書5節（Phase 0）は「プラグイン機構が通常発話・応答側に届くかは未検証」と明記し、検証結果次第でStandalone Relay方式（Phase 1b）に切り替えるとしていた。

### 2.2 Phase 0検証の実施内容

フレッシュclone（`/tmp/kilo/CodeRouter-fresh`、v2.15.0）および `coderouter-plugin-compress` の実コード・ドキュメントで以下3点を確認した：

1. `InputFilter` 基底クラスのシグネチャと呼び出しタイミング
2. `compress` の `targets: [tool_result]` が何を意味し、通常のuser/assistant発話に届くか
3. 応答側（モデル生成後の最終応答）にプラグインからフック可能か

### 2.3 検証結果

#### (1) 入力側フックは存在し、翻訳に使える → YES

- 定義: `coderouter/plugins/base.py:46-72` — `async def transform(self, request: AnthropicRequest) -> AnthropicRequest`。`model_copy(update={...})` で新インスタンスを返す不変契約。
- 呼出点: `coderouter/routing/fallback.py:2936-2941`（非ストリーミング）、`:3286-3290`（ストリーミング）。tool-loopガードの後・chain解決＋context budgetガードの前に実行される。
- 失敗時: 例外→ `input-filter-failed` ログ＋変更前リクエストで継続（`fallback.py:2809-2843`）。FR5（全滅時原文パススルー）の土台になる。
- `async` のため `httpx`（本体の既存依存）による翻訳バックエンド呼び出しは可能で、NFR2（依存追加なし）も満たせる。

#### (2) `targets` はエンジン制限ではない → YES（補正あり）

企画書の問いは「`targets` の受理値全体に通常発話を指す値があるか」だったが、調査の結果、**`targets` はエンジンの概念ではなく `compress` プラグイン内のローカル設定**であることが判明した：

- エンジンは `AnthropicRequest`（`coderouter/translation/anthropic.py:220-`、 `messages: list[AnthropicMessage(role=user|assistant)]`）全体をフィルタに渡し、制限をかけない。
- `compress` の `VALID_TARGETS = ("tool_result",)` は `src/coderouter_plugin_compress/config.py:27` のプラグイン内バリデーションに過ぎず、`filter.py:55-82` は全メッセージ走査＋ `block.get("type") in targets` 判定をしているだけ。
- よって翻訳プラグインが `type==text` かつ `role==user` を対象にすれば通常発話に届く。`system` / `tool_result` / `tool_use.input` を除外する実装も可能で、企画書3.3節のスコープ限定も実現できる。
- `request.profile` が存在するため方式A（プロファイル固定フラグ）も可能。方式B用のCJK判定は `coderouter/language_tax.py` の `_CJK_RANGES` と `token_estimation.extract_text_from_anthropic_request` が流用できる。

#### (3) 応答側フックは存在しない → NO（決定的）

- `OutputFilter` Protocolは存在するが（`base.py:181-195`）、docstringで **"Not yet integrated — Protocol contract only"** と明記。
- `coderouter/plugins/loader.py:48-58` で `output_filter` / `frontend` / `guard` は `PLUGIN_GROUPS_FUTURE` に分類され、インストール＋enableされても `plugin-group-not-yet-active` 警告を出してロードするだけで**フックは一度も呼ばれない**（`loader.py:119-131`、テスト `tests/test_plugins_loader.py:240-` で固定）。
- エンジン内にプラグイン型 `OutputFilter` の呼び出し箇所はゼロ。ヒットするのはコア内蔵の文字列名式フィルタ（`coderouter/output_filters.py` の `KNOWN_FILTERS`＋`OutputFilterChain`）のみで、`validate_output_filters` が未知の名前を `ValueError` で拒否するため、コア改造なし（NFR3厳守）では登録不可。
- 残る `Observer` は `request_completed`（`{request, response, latency_ms, provider}`）を受け取れるが、`asyncio.create_task` のfire-and-forgetで**変異不可**と明記（`base.py:75-98`、`fallback.py:2845-2903`）。ストリーミングではSSE終了後に1回のみ発火する。
- `Adapter` プラグイン（v2.8.0でwired化）は新規 `kind` のバックエンド工場であり、応答の後処理フックではない。

→ **FR2（応答EN→JA）はプラグインとして実装不可**。FR1（入力JA→EN）のみ可能という片肺状態になる。

### 2.4 追加で判明した制約：OpenAI ingressはフィルタを迂回する

- `InputFilter` は **Anthropic ingress（`/v1/messages` → `generate_anthropic` / `stream_anthropic`）でのみ実行**される。OpenAI互換 ingress（`/v1/chat/completions` → `generate` / `stream(ChatRequest)`、`fallback.py:2451-2610`）には `_apply_input_filters` の呼び出しがない。
- 企画書4.1の図は `(OpenAI互換 /v1/chat/completions)` と記載しているが、このままでは翻訳が一切発火しない。AGENTS.mdの「Context BudgetはAnthropic ingressのみ」と同一の制約がここにもある。
- よってPlugin方式でもクライアントには `ANTHROPIC_BASE_URL=http://host:8088/v1`＋Anthropic Messages APIを使わせる必要があり、図の修正が必要になる。

### 2.5 判定

企画書5節の判定ルール（「2・3が両方YES→Phase 1a、いずれかNO→Phase 1b」）に従い、**問3がNOのため Phase 1b（Standalone Relay方式）に切り替える**。Plugin方式に固執すればコアへの `OutputFilter` 配線追加（`loader.py` のFUTURE→ACTIVE移動＋エンジン呼び出し点追加＋テスト）が前提となり、「コア無改造」（NFR3）の利点は失われる。

---

## 3. 採用する構成：Standalone Relay（別アプリ）

```
コーディングエージェント（日本語）
        │  Anthropic Messages API (/v1/messages)
        ▼
┌───────────────────────────────┐
│ [NEW] translate-relay          │  ← 本書の開発対象（別プロセス）
│  1. JA→EN翻訳                  │
│     CodeRouterの "translate"   │
│     プロファイルを叩くことで   │
│     実装（自前フォールバック   │
│     ロジック不要）             │
│  2. 翻訳済み英語をCodeRouter   │
│     の "local-ja-broken"       │
│     プロファイルへ転送         │
│  3. 応答をEN→JA翻訳して返却    │
└──────────────┬────────────────┘
               ▼
      CodeRouter（無改造・既存のまま）
               ▼
      ローカルLLM（REAP-384等）
```

利点：

- フォールバック機構を新規に書かない（翻訳・本処理ともCodeRouterのプロファイルに委譲）。
- 新規コードは実質「プレースホルダ保護（企画書4.3節）」＋「2回のHTTP呼び出しの中継」のみ。
- CodeRouter・クライアント双方のAPI形状を変えない。Relayを止めれば元の直結運用に即時復帰できる（失敗してもモデルを壊さない）。
- 応答側・ストリーミング応答の扱いをRelay内で自由に設計できる（Plugin方式の最大欠点を回避）。

---

## 4. ポート設計：CodeRouterと競合させないための示唆

### 4.1 現状のポート整理（実測値）

| プロセス | ポート | 根拠 |
|---|---|---|
| `coderouter serve`（既定値） | `4000` | `coderouter/cli.py:54`（`--port` 既定） |
| `coderouter.service`（稼働中） | **`8088`** | `~/.config/systemd/user/coderouter.service` の `ExecStart=... serve --port 8088` |
| `coderouter-webui.service` | **`8089`**（環境変数 `CR_WEBUI_PORT` で可変） | `webui/server.py:19-22`（`BACKEND_PORT=8088`、`BIND_PORT=8089`） |
| ローカルバックエンド群（llama-server / LM Studio / Ollama等） | 各自の固定ポート | CodeRouterの `providers.yaml` の `base_url` が参照。Relayは直接触らない |

教訓：CodeRouterは既定 `4000` と運用 `8088` の2つの顔を持つ。ドキュメントのポート番号だけを見て空き判断をすると誤るため、**必ず稼働中のsystemd unitと `providers.yaml` の `base_url` を正とする**こと。

### 4.2 Relay用ポートの選定

- **推奨: `8090`**（`8088`/`8089` の隣接・未使用帯）。代替候補 `18088`（将来 `8088` 系が増えても衝突しにくい）。
- **ハードコード禁止**。`TRANSLATE_RELAY_PORT`（例：既定 `8090`）環境変数で上書き可能にし、`webui/server.py` の `CR_WEBUI_PORT` パターンに倣う。
- `providers.yaml` の翻訳・本処理プロファイルが指すのは **CodeRouterの `8088` のまま**。RelayはCodeRouterのポートを変更しない（既存クライアント・WebUI・ヘルスチェックへの影響ゼロ）。

### 4.3 競合回避の実装チェックリスト

1. **起動時バインド検証**：Relay起動時に `socket.bind` で `EADDRINUSE` を即時検出し、「競合プロセス名（`ss -ltnp` 相当のヒント）と代替ポート」を添えて異常終了する。黙って別ポートにフォールバックしない（クライアントの向き先がずれる事故を防ぐ）。
2. **SO_REUSEADDRに頼らない**：再起動直後のTIME_WAIT誤認を避けるため、`allow_reuse_address = False` 相当で起動し、systemdの `RestartSec=3` と組み合わせる。
3. **リッスンアドレス**：既定は `127.0.0.1`（単一ホスト運用）。LAN公開が必要な場合のみ `--host 0.0.0.0` を明示し、その際は `CODEROUTER_ALLOWED_HOSTS` と同様のホスト許可リストをRelayにも持たせる。
4. **クライアントの向き先切替は1箇所**：エージェントの `ANTHROPIC_BASE_URL` を `http://host:8088/v1` → `http://host:8090/v1` に変えるだけ。CodeRouter直結に戻す場合も同1行の差し戻しで済む。
5. **ループ防止**：Relayの転送先は必ず `8088` のプロファイル名（`translate` / `local-ja-broken`）とし、Relay自身（`8090`）を `base_url` に書かない。設定ロード時に自己参照を検出したら起動失敗させる。
6. **ヘルスチェック分離**：Relayは `/healthz`（Relay自身）と `/healthz/downstream`（CodeRouter `8088` への到達性）を分け、後者失敗時はRelayを落とさず `degraded` 応答＋ログに留める（CodeRouter再起動時にRelayが道連れにならない）。
7. **systemd unit分離**：`translate-relay.service` を新設し、`After=coderouter.service` / `Wants=coderouter.service` とする。`coderouter.service` 本体には手を触れない（ポート・ExecStart不変）。
8. **ログの区別**：Relayのログプレフィクスを `translate-relay` に固定し、CodeRouterの `coderouter-*` ログと `journalctl` で区別できるようにする。翻訳フォールバック発生時は使用バックエンド名を必ず記録する（企画書8節の観測項目）。

### 4.4 将来Plugin方式に戻る条件

CodeRouter本体で `OutputFilter`（`coderouter/plugins/base.py:181-`）がwired化され、`PLUGIN_GROUPS_FUTURE` からACTIVEに昇格したバージョンがリリースされた場合に限り、Relay内の応答側ロジックをプラグインとして移植する選択肢が生まれる。その際は本書2.3の検証を同バージョンで再実施すること（単独開発OSSのためインターフェース変更があり得る）。

---

## 5. 参考

- 企画書兼仕様書： `webui/docs/coderouter-plugin-translate_企画書仕様書.md`
- CodeRouter本体（検証時）： `https://github.com/zephel01/CodeRouter` v2.15.0
- compressプラグイン（実装例）： `https://github.com/zephel01/coderouter-plugin-compress`
- AdaptiveAPI（設計参考のみ、不採用）： `https://github.com/DeeJayTC/AdaptiveAPI`
- 本書は2026-09-03時点のソースコードに基づく。実装着手時に最新版での再確認を推奨。
