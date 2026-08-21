# 民泊シミュレーター

単一HTML（`minpaku-simulator.html`）+ Firebase RTDB のPWA。Netlifyで配信。

## 「民泊チェック」と言われたら

必ず `.claude/skills/minpaku-check/SKILL.md` の手順に従うこと。要点:

- **5ソース全部を見る**: シミュレーター / Airbnb / Booking.com / PriceLabs / Beds24。
  一部だけ見て結論を出さない。
- **読めないデータは推測で埋めない**。「未取得」と明記し、見える分でレポートした上で、
  末尾に「このデータがあればさらに分かること」を必ず付ける。
- **PriceLabsの稼働率はブロックを稼働扱いする**ため、マンスリー貸しがある物件では
  実予約（Beds24/シミュレーター）と必ず突き合わせる。
- **マンスリー（28泊以上）を必ず確認**。契約書はGoogle Drive（`一時使用`/`Lease`で検索）。
  シミュレーター上はAirbnb予約として入り、ADR・稼働からは除外・売上のみ計上される設計。

データ収集はユーザーのMacで `python3 collect_minpaku_data.py` → Drive「民泊バックアップ」。
この実行環境から Beds24 API / Firebase へは直接届かない（403）。

## 実装メモ

- 予約データ: `getAllBookings()` = airbnbBookings + bookingComBookings（`minpaku-simulator.html`）
- 長期判定: `isLongStayListing()` = リスティング名`【28泊以上】` or 28泊以上
- バックアップ: 設定タブ「💾 バックアップ」→ `monthlyData` 含む全データJSON
- `fetch_beds24.py`: Booking.com予約のみ注入（Airbnb分は取得するが未使用）
