# 08. RSS フィード検証結果と採用リスト

検証日: **2026-11-22（日曜）** / 検証方法: `curl` で実取得 → XML パース → 件数・最新日時・要約長を計測

> ⚠️ **検証日が日曜のため「24h内件数」は参考値にすぎません。** 米国市場が休場で経済ニュースの流量が平日より大幅に少ない状態です。**平日に再検証して本ドキュメントを更新する必要があります**（[00](00-open-items.md) V-7）。件数以外の指標（フィードの生死・要約の有無・最新日時）は曜日に影響されないため、採否の判断には使えます。

---

## 1. 🔴 最重要の発見: WSJ のフィードは停止している

**WSJ の RSS は HTTP 200 を返すが、中身が約 22 か月前で凍結している。**

```
https://feeds.a.dj.com/rss/RSSMarketsMain.xml
  → HTTP 200 / 20件 / 最新 pubDate: Mon, 27 Jan 2025 14:27:15 -0500
```

これは**最も危険な故障モード**です。HTTP ステータスだけを見る実装では「正常」と判定され、**2 年前のニュースを「過去 24 時間の記事」として AI に渡してしまいます**。幸い 24 時間の時間窓フィルタ（[04](04-phase1-plan.md) 処理フロー 3）で除外されますが、気づかないまま「なぜか WSJ の記事が 1 件も入らない」状態が続きます。

### 設計への反映

この発見を受けて、以下を Phase 1 の実装に追加します。

1. **WSJ は採用しない**（下記 §3）
2. **フィード鮮度の監視を入れる** — 各フィードの最新記事が 48 時間以上古い場合、警告ログを出す。Phase 2 では通知対象に含める
3. **「0 件のフィード」を正常扱いしない** — 全フィードが 0 件なら処理を失敗させる

> **教訓として記録:** RSS の死は 404 ではなく「200 を返しながら古いデータを配る」形で起きる。HTTP ステータスの確認だけでは不十分。

---

## 2. 検証結果の全体像

| フィード | HTTP | 件数 | 最新 | 要約長 | 判定 |
|---|---|---|---|---|---|
| **CNBC Top News** | 200 | 30 | 5.7h前 | 119字 | ✅ **採用** |
| **CNBC Economy** | 200 | 30 | 20.6h前 | 115字 | ✅ **採用** |
| **CNBC Markets** | 200 | 30 | 29.7h前 | 129字 | ✅ **採用** |
| **NYT Business** | 200 | 47 | 1.7h前 | 142字 | ✅ **採用** |
| **NYT Economy** | 200 | 20 | 12.5h前 | 144字 | ✅ **採用** |
| **MarketWatch Top Stories** | 200 | 10 | 10.5h前 | 94字 | ✅ **採用** |
| **BBC Business** | 200 | 58 | 4.3h前 | 105字 | ✅ **採用** |
| **Federal Reserve 全プレスリリース** | 200 | 20 | 38.5h前 | 111字 | ✅ **採用** |
| Fed 金融政策のみ | 200 | 15 | 88.7h前 | 74字 | ⬜ 不採用（上記に包含） |
| **The Guardian Business** | 200 | 37 | 0.3h前 | **665字** | ✅ **採用**（§4-1） |
| **Financial Times** | 200 | 9 | 0.7h前 | 101字 | ✅ **採用**（§4-2・ペイウォール明示） |
| **Seeking Alpha Market Currents** | 200 | 7 | 9.2h前 | **なし** | ✅ **採用**（§4-3） |
| **WSJ Markets / World** | 200 | 20 | **15,928h前** | 116字 | ❌ **除外（§1）** |
| Investing.com News | 200 | 10 | 0.2h前 | **なし** | ❌ 除外（§5） |
| CNN Money | 200 | 1 | **70,623h前** | 157字 | ❌ 除外（事実上停止） |
| Yahoo Finance News | **404** | — | — | — | ❌ 除外（§5） |
| Yahoo Finance 代替2種 | 404 / 429 | — | — | — | ❌ 除外 |
| **Reuters** | **404** | — | — | — | ❌ **除外（§5）** |
| MarketWatch MarketPulse | 400 | — | — | — | ❌ 除外 |
| BLS（米労働統計局） | **403** | — | — | — | ❌ 除外（§5） |
| AP News / Treasury | 403 / 接続不可 | — | — | — | ❌ 除外 |

---

## 3. 採用リスト（11 本）

`resources/feeds.toml`（当初 `config/feeds.toml`）の初版案。要約（`description`）を持ち、鮮度が確認できたものに絞りました。

| # | 名称 | URL | weight | 役割 |
|---|---|---|---|---|
| 1 | CNBC Top News | `https://www.cnbc.com/id/100003114/device/rss/rss.html` | 1.0 | 市場全般の速報 |
| 2 | CNBC Economy | `https://www.cnbc.com/id/20910258/device/rss/rss.html` | 1.0 | 経済指標・金融政策 |
| 3 | CNBC Markets | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | 1.0 | 株式・債券市場 |
| 4 | NYT Business | `https://rss.nytimes.com/services/xml/rss/nyt/Business.xml` | 0.9 | 企業・業界動向 |
| 5 | NYT Economy | `https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml` | 0.9 | マクロ経済 |
| 6 | MarketWatch Top Stories | `http://feeds.marketwatch.com/marketwatch/topstories/` | 0.9 | 市場解説 |
| 7 | BBC Business | `https://feeds.bbci.co.uk/news/business/rss.xml` | 0.8 | 国際・世界経済 |
| 8 | **Federal Reserve** | `https://www.federalreserve.gov/feeds/press_all.xml` | **1.2** | **一次情報** |
| 9 | Financial Times | `https://www.ft.com/rss/home` | 1.0 | 金融専門（**要購読**） |
| 10 | Seeking Alpha Market Currents | `https://seekingalpha.com/market_currents.xml` | 0.8 | 市場速報（ティッカー付・要約なし） |
| 11 | The Guardian Business | `https://www.theguardian.com/uk/business/rss` | 0.7 | 欧州視点（要約を200字に切詰） |

### 選定の考え方

**Fed に最も高い weight（1.2）を付ける理由:** 本システムの要件は「**事実と市場予想とのズレ**」の分析です。Fed のプレスリリースは一次情報であり、報道各社の解釈が入る前の事実そのもの。重複排除で代表記事を選ぶ際に、同じトピックなら一次情報を優先させたい。

**CNBC を 3 本入れる理由:** 米国市場に最も密着しており、Top / Economy / Markets でカバー範囲が異なります。重複は排除ロジックで処理されるため、取りこぼしを減らす方を優先します。

**BBC を入れる理由:** 「**世界**のニュース」という要件に対し、米国紙だけでは欧州・アジアの動きが拾えません。米国偏重を補正します。

**MarketWatch の URL が `http://` な点 [要確認]:** リダイレクト先が HTTPS かを Phase 1 の実装時に確認し、HTTPS 直指定に変更できるなら変更します。

---

## 4. 採用した 3 本（条件付き・2026-11-22 決着）

### 4-1. The Guardian Business — 要約が 665 字と長い

| | |
|---|---|
| 状態 | ✅ 正常（37件、最新 0.3h前） |
| 問題 | 要約が平均 **665 字**。他フィードの約 6 倍 |
| コスト影響 | 全記事がこの長さだと入力トークンが増える。ただし重複排除後 15 件なので影響は限定的（+$0.01/回 程度） |
| 価値 | 無料・ペイウォールなし。英国視点の経済報道 |

**推奨: 要約を先頭 200 字で切り詰めて採用。** 情報量は十分で、トークン増加も抑えられます。切り詰めは `processing/normalize.py` に入れる処理で、全フィードに一律適用します。

### 4-2. Financial Times — ペイウォール

| | |
|---|---|
| 状態 | ✅ 正常（9件、最新 0.7h前、要約101字） |
| 問題 | **FT は購読者限定**。レポートからリンクしても閲覧者が読めない |
| 価値 | 金融専門メディアとして分析の質が高い |

**推奨: 採用するが、レポート上で「購読が必要」と明示する。** 要約を AI の分析材料に使うことは有益で、リンク先が読めないことは明示すれば実害がありません。

→ **採用で決定**（2026-11-22）。レポートの出典表記に「要購読」ラベルを付ける。

### 4-3. Seeking Alpha Market Currents — 要約がない代わりにティッカー情報を持つ

```xml
<item>
  <title>Senate passes bipartisan bill to cut permitting times for energy projects...</title>
  <category domain="https://seekingalpha.com/symbol/XLE">xle</category>
  <category domain="https://seekingalpha.com/symbol/XLU">xlu</category>
  <pubDate>Sat, 21 Nov 2026 21:30:34 -0500</pubDate>
</item>
```

| | |
|---|---|
| 状態 | ✅ 正常（7件、最新 9.2h前） |
| 問題 | `description` が**ない**。タイトルのみ |
| 独自の価値 | **関連ティッカーが `category` で付く**（上例では XLE / XLU / XOP）。どのセクターに効く材料かが機械的に分かる |

**推奨: 採用する。** タイトルのみでも Seeking Alpha の見出しは具体的（上例は「上院が許認可短縮法案を可決」）で、分析材料になります。さらに**セクター情報は S&P500 への波及メカニズム分析に直接役立つ**ため、AI への入力に `related_tickers` として渡す価値があります。

→ **採用で決定**（2026-11-22）。要約なし記事を許容する分岐と、`category` からのティッカー抽出を実装する（F-4 / F-5）。

---

## 5. 除外したフィードと理由

### 5-1. ⚠️ Reuters — 公開 RSS は利用不可（HTTP 404）

```
https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best
  → HTTP 404
```

**当初ご指定いただいた 3 媒体のうち Reuters は使えません。** 公開 RSS の提供が終了しているものと見られます **[推定: 他のエンドポイントが存在する可能性は否定できない]**。

代替として CNBC・NYT・MarketWatch・BBC でカバーします。Reuters の記事は他媒体経由で間接的に触れられることが多く、また Fed の一次情報を入れたことで「速報性のある事実」の取得は担保できています。

> Reuters のコンテンツが必須であれば、Reuters Connect などの**有料 API** が選択肢になります。月額は本システムの他の費用を大きく上回る可能性が高いため、現時点では推奨しません。

### 5-2. ⚠️ Yahoo Finance — ニュース RSS は利用不可（404 / 429）

3 つの URL パターンを試して全て失敗しました。**ニュース取得源としては使えません。**

**ただし市況データ取得（`yfinance` による `^GSPC` / `^TNX` / `^VIX`）は別系統であり、そちらは引き続き使用します。** ご指定の「Yahoo Finance」は市況データの用途では機能します。

### 5-3. BLS（米労働統計局）— HTTP 403

User-Agent を変えても 403。プログラムからのアクセスがブロックされています。雇用統計・CPI は市場への影響が大きいため残念ですが、**これらは Fed のプレスリリースや CNBC Economy で報道ベースで捕捉できます**。

**[要確認]** BLS は RSS 以外に公式 API を提供しています。一次情報として雇用統計・CPI を直接取得したい場合、Phase 2 以降で BLS API の利用を検討する余地があります（登録が必要）。現時点ではスコープ外とします。

### 5-4. Investing.com — 要約なし + ノイズが多い

`description` がないうえ、取得した記事の内容が「Fortinet CFO が 27,223 ドル分の自社株を売却」といった**インサイダー取引の個別報告**でした。S&P500 全体への影響を分析する本システムには信号対雑音比が低すぎます。

---

## 6. `resources/feeds.toml` 初版案

```toml
# 検証日: 2026-11-22（日曜）。平日に再検証が必要
# weight: 重複排除で代表記事を選ぶ際の媒体優先度

[[feed]]
name = "Federal Reserve"
url = "https://www.federalreserve.gov/feeds/press_all.xml"
weight = 1.2            # 一次情報を最優先
category = "primary"

[[feed]]
name = "CNBC Top News"
url = "https://www.cnbc.com/id/100003114/device/rss/rss.html"
weight = 1.0
category = "market"

[[feed]]
name = "CNBC Economy"
url = "https://www.cnbc.com/id/20910258/device/rss/rss.html"
weight = 1.0
category = "economy"

[[feed]]
name = "CNBC Markets"
url = "https://www.cnbc.com/id/10000664/device/rss/rss.html"
weight = 1.0
category = "market"

[[feed]]
name = "NYT Business"
url = "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"
weight = 0.9
category = "business"

[[feed]]
name = "NYT Economy"
url = "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"
weight = 0.9
category = "economy"

[[feed]]
name = "MarketWatch Top Stories"
url = "http://feeds.marketwatch.com/marketwatch/topstories/"
weight = 0.9
category = "market"

[[feed]]
name = "BBC Business"
url = "https://feeds.bbci.co.uk/news/business/rss.xml"
weight = 0.8
category = "world"

[[feed]]
name = "Financial Times"
url = "https://www.ft.com/rss/home"
weight = 1.0
category = "finance"
paywall = true          # レポートの出典表記に「要購読」ラベルを付ける

[[feed]]
name = "Seeking Alpha Market Currents"
url = "https://seekingalpha.com/market_currents.xml"
weight = 0.8
category = "market"
has_summary = false     # タイトルのみ。category からティッカーを抽出する

[[feed]]
name = "The Guardian Business"
url = "https://www.theguardian.com/uk/business/rss"
weight = 0.7
category = "world"
```

---

## 7. 実装への反映事項

この検証から Phase 1 の実装に追加する項目:

| # | 項目 | 理由 |
|---|---|---|
| F-1 | **フィード鮮度チェック** — 最新記事が 48h 以上古いフィードは警告 | §1 の WSJ 問題。200 を返す死んだフィードを検知する |
| F-2 | **全フィード 0 件なら処理を失敗させる** | 無言のデータ欠落を防ぐ |
| F-3 | **要約の先頭 200 字で切り詰め** | §4-1。Guardian 等の長い要約でトークンを無駄にしない |
| F-4 | **要約なし記事の許容** | §4-3。Seeking Alpha をタイトルのみで扱う |
| F-5 | **`category` 情報からティッカーを抽出** | §4-3。セクター情報を AI 入力に含める |
| F-6 | **User-Agent を明示的に設定** | 複数フィードがデフォルト UA を拒否。ただし BLS は UA 変更でも 403 |
| F-7 | **フィード個別の失敗を許容し、ログに残す** | 1 本の障害で全体を落とさない（既定方針） |

## 8. 再検証の予定

- [ ] **平日（米国市場営業日）に再検証**し、24h 内件数の実態を確認する
- [ ] 重複排除後の件数が [03](03-cost-and-tokens.md) の想定（15 件）と整合するか確認
- [ ] 想定より多ければ `max_articles_to_analyze` で上限を調整
