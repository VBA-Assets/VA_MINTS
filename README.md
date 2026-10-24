# VA mints — mints 提出前のファイル確認ツール

VA mints は、民事裁判書類電子提出システム「mints」の「記録」への提出前に、フォルダ単位でファイル群の内容を確認し、mints の制約（用紙サイズ・ファイル名・パスワードの有無・証拠番号標記など）に適合しているかを一括してチェックする、Windows 用の無料ツールです（Python 製）。

- 配布：[VBAアセット](https://vba-assets.net/)（実行ファイルとインストーラーは、サイトの記事からダウンロードしてください）
- ライセンス：MIT License（[LICENSE](LICENSE)）
- 操作マニュアル：[docs/manual/VA_mints_操作マニュアル.md](docs/manual/VA_mints_操作マニュアル.md)
- 要件定義：[docs/03_requirements.md](docs/03_requirements.md)

## フォルダの構成

| フォルダ・ファイル | 内容 |
|---|---|
| `src/` | 本体のソース |
| `tests/` | テスト（pytest） |
| `samples/` | 手動テスト用の合成データ（実際の証拠・書面の内容は含まない） |
| `scripts/` | ビルド・サンプル生成などのスクリプト |
| `config.yaml` | 上限値などの設定 |

開発用の内部資料（開発の指示書、企画書、制約の調査報告）と、mints の一次資料（裁判所のマニュアルなど。著作権のため）は、このリポジトリには含めていません。ソースのコメントでそれらを参照している箇所があります。

## ソースから動かす

Windows 10・11 と Python 3 で動かします。

```
python -m pip install -r requirements.txt
python main.py
```

テストは `python -m pytest` で走ります。実行ファイルは `pyinstaller mints_prep.spec` で `dist\VA_mints.exe` にできます（`config.yaml` を同じフォルダに置いて使います）。

### Mac で動かす（動作は保証しません）

Mac 用の実行ファイルは配っていません。Python 3（Tk の入ったもの。python.org の macOS 用のインストーラーの Python には入っています）で、ソースから動かしてください。

```
python3 -m pip install -r requirements.txt
python3 main.py
```

- 画像（JPEG・PNG）に押す証拠番号の書体は、Windows ではメイリオ、Mac ではヒラギノ角ゴシック（Mac に標準で入っているもの）を使います。字の形だけが違います。PDF に押す証拠番号は、Windows と同じです。
- ［フォルダを開く］は、Finder で出力フォルダを開きます。
- Mac のファイル名は、濁点・半濁点を分けた形で保存されていることがあります。文字数と JIS X 0213 外の文字の判定は、濁点・半濁点を合わせた形で行います。ファイル名整形で名前を変えるときも、合わせた形で書き出します。
- Mac の実機では動作を確かめていません。
