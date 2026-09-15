# AIに機密情報を持たせる方法 ― 付属アプリ

書籍『AIに機密情報を持たせる方法 ― 自分だけのローカルAIでRAG構築』（江藤 潔）で作る、自分のPC一台で完結するRAG（検索拡張生成）アプリです。自分の資料（Word / PDF / Excel）を検索し、その内容だけを根拠に日本語で答え、根拠にした資料名とページを示します。資料も質問も、PCの外には一切送りません。

- 言語モデルの実行: [Ollama](https://ollama.com/)（GPU不要、CPUだけで動作）
- 日本語の分割・検索・並べ替え: [KotobaCore](https://github.com/ekiyo55/kotobacore)
- 画面: FastAPI + ブラウザ（ビルド不要）

## 動作環境

- Windows 11（主）／ macOS（同じ手順で動作。Ollamaが内蔵GPUを自動使用）／ Linux（参考）
- Python 3.10 以上（検証機は 3.13）
- メモリ 16GB 推奨（8GB でも動作、32GB あれば余裕）
- 検証機: Core i5-1240P、16GB、単体GPUなし

## 準備（書籍 第2章）

```text
pip install kotobacore fastapi uvicorn jinja2 python-multipart python-docx pypdfium2 openpyxl
ollama pull qwen3:1.7b
ollama pull bge-m3
```

`qwen3:1.7b` が回答を書く言語モデル、`bge-m3` が資料を「意味の近さ」で探すための埋め込みモデルです。

## 起動と停止

`documents/` に資料（.docx / .pdf / .xlsx）を入れてから:

- Windows: `start.bat` をダブルクリック（サーバーを起動し、ブラウザで http://127.0.0.1:8000/ を開きます）
- 停止: `stop.bat` をダブルクリック（ブラウザを閉じてもサーバーは残るため、明示的に止めます）
- 手動なら `python run.py`

初回起動時は資料の埋め込みを計算するため、資料の量に応じて時間がかかります（検証用の8資料・853チャンクで約10分）。2回目以降は `embed_cache.json` から読み込むので約20秒で起動します。資料を差し替えると、変わったファイルだけ再計算されます。

## 資料セットの切り替え（書籍 第8章）

資料フォルダは環境変数 `KIMITSU_DOCS` で切り替えられます。未設定なら `documents/`（社内規程）を読み、`documents_research/` を指せば研究・調査用の資料セットを読みます。「原本を開く」のリンク先も一緒に切り替わります。Windows では `start_research.bat` をダブルクリックすると研究用の資料セットで起動します。

```text
set KIMITSU_DOCS=C:\path\to\kimitsu-rag\documents_research
python run.py
```

目的の違う資料を一つの索引に混ぜず、フォルダで分けるのが本書の方針です（前著 9.5・姉妹書 Q71 の「目的別に資料を絞る」の実装）。研究用の資料の入手先は `documents_research/README.md` にあります。

## チームで共有する（書籍 第9章）

1台のPCを社内LAN内の「共有サーバー役」にします。`start_lan.bat` をダブルクリックすると、環境変数 `KIMITSU_HOST=0.0.0.0` で起動し、このPCのIPv4アドレスを表示します。同僚のブラウザからは `http://（そのアドレス）:8000/` で同じ画面が開きます。

- 初回起動時に Windows のファイアウォールが python.exe の通信許可を尋ねてきたら、**プライベートネットワークのみ**許可してください（パブリックは許可しない）。既存の許可は PowerShell の `Get-NetFirewallRule -DisplayName Python | Format-Table DisplayName, Profile, Enabled` で確認でき、パブリックの規則が残っていれば無効化してください
- 待ち受けアドレスは環境変数 `KIMITSU_HOST`（既定 `127.0.0.1`＝自分のPCだけ）、ポートは `KIMITSU_PORT`（既定 `8000`）
- **インターネットには絶対に公開しないでください。** ログイン機能はなく、同じLANの中にいる人は誰でも使える前提の設計です
- 停止は `stop.bat`（待ち受けアドレスに関係なく、ポート8000のサーバーを止めます）
- `history.db` には全員の質問と回答（引用された条文を含む）が残ります。共有ホストのこのファイルは、資料と同じ機密性で扱ってください

## 育てる（書籍 第10章）

- 資料を差し替えたら起動し直すだけ。変わったファイルだけ再計算されます（SHA-256で検知）
- よくある質問と担当者が確認した答えを `総務FAQ.docx` のような文書にして `documents/` に置くと、次からその答えが根拠として上位に来ます
- 月に一度 `python review_history.py` で履歴を見返せます。「判断できません」と答えた質問、2回以上聞かれた質問、根拠が付かなかった回答、資料ごとの参照回数を表示します（FAQ に足す質問を選ぶ材料）
- モデルや Ollama、ライブラリを更新したら `python replay_history.py 5` で、履歴にある直近の質問を再実行し、当時の答えと今の答えを見比べられます（根拠の資料名が一致するかを先に表示）
- 動作確認済みのライブラリの版は `requirements-lock.txt` に固定してあります。同じ版で揃えるなら `pip install -r requirements-lock.txt`

## 構成

```text
main.py            FastAPI アプリ（画面・API・質問履歴）
rag_engine.py      資料の読み込み → チャンク分割 → 検索 → 生成
replay_history.py  履歴の質問を再実行して答えの変化を確認（更新後の回帰確認）
review_history.py  月次の見返し（判断できません／繰り返し質問／根拠なし／資料別の参照回数）
requirements-lock.txt  検証機で動作した版の固定リスト
run.py / start.bat / stop.bat / start_research.bat / start_lan.bat
templates/         チャット画面・履歴画面（Jinja2）
static/            CSS・JS
documents/         検索対象の資料（サンプルの出典は documents/README.md）
history.db         質問履歴（SQLite。自動生成・.gitignore 対象）
embed_cache.json   埋め込みキャッシュ（自動生成・.gitignore 対象）
```

## 質問履歴の扱いについて

`history.db` には質問と回答が保存されます。回答には資料の文言がそのまま含まれることが多いため、このファイルは元の資料と同じ機密性で扱ってください（共有フォルダに置かない、バックアップの取り扱いに注意する）。

## ライセンス

Apache License 2.0（`LICENSE` を参照。検索エンジンの KotobaCore、既定モデルの Qwen3 と同じライセンスです）。サンプル資料の著作権は各出典元にあります。
