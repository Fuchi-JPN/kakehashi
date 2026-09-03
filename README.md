# Kakehashi（かけはし） Alpha 0.1

日英⇄英日 自動翻訳APIプロキシ。ハーネス（Kilo Code等）は `http://<host>:8090/v1` のみを参照する。

## 開発に至った経緯

### 背景：メモリに収めるための枝刈りと、日本語性能の破綻

ローカルLLMを手元のマシンで実用速度で動かすには、モデルのメモリ占有を物理メモリに収めることが絶対条件である。180B級モデルをそのまま載せることはできず、量子化に加えてMoEエキスパートの枝刈り（pruning）が避けられない。そして枝刈りは英語・コード性能をほぼ維持できる一方、日本語生成を破綻させがちである。モデルを作り直すのではなく、入出力をプロキシ層で翻訳して迂回する——それがKakehashiである。

### 対象モデル：Qwen3.8-Flash-Next-REAP-288-MLX-4bit

開発の直接の動機となったモデルが、sh0wie氏の `Qwen3.8-Flash-Next-REAP-288-MLX-4bit` である（詳細は `docs/sh0wieQwen3.8-Flash-Next-REAP-288-MLX-4bit · Hugging Face.md` に収録）。

- Qwen3.8-Flash-Next（125B本体＋51B n-gramテーブル、48層・288エキスパートMoE top-10）を、量子化済み重み上で較正したREAP saliencyにより512→288エキスパートへ枝刈りしたMLX 4-bitビルド
- ディスク 98GB→68GB、常駐メモリ 97GB→68GB（n-gramテーブルをNVMeに置く39GBモードあり）、HumanEval pass@1 93.9%→91.5% を維持
- M4 Maxで約28 tok/sデコード、MTP speculative decoding対応、per-layer kept-expert manifest（`reap_kept_experts.json`）で再現可能
- ソース変換の2欠陥（RMSNormの非ゼロ中心化、n-gramテーブルのモジュールパス不一致）を重み側で修正済みのため、stock mlx-vlmで無パッチ動作する

本プロジェクトの運用環境は **メモリ64GBのMacBook Pro M1 Max** であり、このクラスのモデルを載せるには上記のような枝刈りビルドが前提となる。英語推論・コーディング性能は健全だが、日本語生成は破綻する——Kakehashiはこの一点をプロキシ層の翻訳で迂回するための常駐アプリである。

### 見通し：枝刈りは今後も避けられず、日本語破綻は続く

ローカルLLMの技術がさらに進歩しても、モデル規模の拡大はメモリ容量の伸びを上回り続ける。限られたメモリに収めるためにはREAPのような枝刈り技術の採用が避けられず、その較正分布が英語・コード中心である限り、結果的に日本語性能が破綻しがちであり続けるだろう。Kakehashiのアプローチ（重みに触らず入出力で迂回する）は、この構造的制約に対する息の長い解である。

> [!NOTE]
> 本リポジトリにはAPIキー等の秘密情報を含みません。設定ファイル（`~/.config/kakehashi/config.yaml`）とログはリポジトリ外に保存されます。
>
> This repository contains no secrets. Config and logs live outside the repo.
>
> License: MIT（`LICENSE` 参照）

## 構成

- 標準: `ハーネス → Kakehashi(8090) → CodeRouter(8088) → ローカルLLM`
- 直接: `ハーネス → Kakehashi(8090) → ローカルLLM / クラウドAPI`

## 起動

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
python -m kakehashi serve --port 8090
# または
kakehashi serve
```

環境変数: `KAKEHASHI_PORT`（既定8090）、`KAKEHASHI_CONFIG`（既定 `~/.config/kakehashi/config.yaml`）。

## ハーネス設定

| 項目 | 値 |
|---|---|
| APIエンドポイント | `http://<host>:8090/v1` |
| APIキー | Kakehashiで有効化時のみ |
| モデル名 | 任意（KakehashiがEgress選択モデルで上書き） |

## Web UI

`http://<host>:8090/ui` でProviders（Egress）/ Backends（翻訳）/ Logging / Serverを管理。再起動不要。

## 障害時切戻し

Kakehashi停止時はハーネスのエンドポイントを上流直結（CodeRouter `http://<host>:8088/v1` 等）に戻す（1行変更）。

## テスト

```bash
pytest -q
```
