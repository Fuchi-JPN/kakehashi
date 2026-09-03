# Kakehashi（かけはし） Alpha 0.1

日英⇄英日 自動翻訳APIプロキシ。ハーネス（Kilo Code等）は `http://<host>:8090/v1` のみを参照する。

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
