# 撤回論文の汚染チェッカー

撤回された論文が、今も「根拠」として引用されている。撤回後の引用を、論文・雑誌・日本の機関ごとに数えました。

公開ページ：https://arai-sarai-data.github.io/retraction-check/

## 仕組み

- `fetch.py` が公式の公開データを取得・検証・集計し、`docs/data/` に JSON を書き出します。
- GitHub Actions（`.github/workflows/update.yml`）が毎月2日に自動で実行し、GitHub Pages に公開します。
- 元データの形式が想定と違うときは **数字を推測で埋めずに停止** します（前回の正しいデータが表示され続けます）。停止した理由は Actions のログに `データ検証で停止しました` として出ます。

## 最初の設定（1回だけ）

1. このフォルダの中身を `arai-sarai-data/retraction-check` リポジトリ（Public）に置く。
2. Settings → Pages → Build and deployment の Source を **GitHub Actions** にする。
3. Settings → Actions → General → Workflow permissions を **Read and write permissions** にする。
3-1. Settings → Secrets and variables → Actions に `OPENALEX_KEY` を登録：OpenAlex の API キー（任意。https://openalex.org/settings/api で取得。なくても動きます）
4. Actions タブ → `update` → **Run workflow** で初回のデータ取得を実行する。

## 出典

- Retraction Watch Database（Crossrefが無料公開）の撤回論文と、OpenAlexの引用データを突き合わせ、撤回の後も引用され続けている論文を示します

各データの利用条件に従い、出典を明記して加工・公開しています（`docs/about.html`）。
