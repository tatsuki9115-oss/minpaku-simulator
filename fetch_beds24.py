#!/usr/bin/env python3
from __future__ import annotations
"""
Beds24 Booking.com予約フェッチスクリプト
使い方: python3 fetch_beds24.py
→ JavaScriptコードを出力するので、osascriptで民泊シミュレーターに注入する
"""

import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

# ==================== 設定 ====================

# リフレッシュトークンの保存場所
TOKEN_FILE = Path.home() / '.beds24_refresh_token'

# Beds24 propertyId → シミュレーターのpropId マッピング
PROPERTY_MAP = {
    327669: 1,  # TY Takahatafudo Stay → ATTビル（高幡不動）
    # 329xxx: 2,  # 新子安（将来追加時）
}

# ==================== 認証 ====================

def load_refresh_token() -> str:
    if not TOKEN_FILE.exists():
        print(f"エラー: リフレッシュトークンファイルが見つかりません: {TOKEN_FILE}", file=sys.stderr)
        print("セットアップ方法: Beds24ダッシュボードで招待コードを生成し、fetch_beds24_setup.py を実行してください", file=sys.stderr)
        sys.exit(1)
    return TOKEN_FILE.read_text().strip()


def get_access_token(refresh_token: str) -> str:
    req = urllib.request.Request(
        "https://api.beds24.com/v2/authentication/token",
        headers={"refreshToken": refresh_token}
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if not data.get('token'):
        raise RuntimeError(f"トークン取得失敗: {data}")
    return data['token']


# ==================== APIフェッチ ====================

def fetch_bookings(token: str) -> list[dict]:
    """Beds24から全予約を取得"""
    url = "https://api.beds24.com/v2/bookings?limit=200&departureFrom=2024-01-01"
    req = urllib.request.Request(url, headers={"token": token})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get('data', [])


# ==================== データ変換 ====================

def convert_booking(b: dict) -> dict | None:
    """Beds24予約 → シミュレーター形式"""
    prop_id = PROPERTY_MAP.get(b.get('propertyId'))
    if prop_id is None:
        return None  # マッピング外の物件はスキップ

    # チャネル判定
    channel = b.get('channel', '')
    source = 'booking' if channel == 'booking' else 'airbnb'

    # チェックイン/チェックアウト日時（YYYY-MM-DD → YYYY/M/D）
    def fmt_date(s):
        if not s: return None
        parts = s.split('-')
        return f"{parts[0]}/{int(parts[1])}/{int(parts[2])}"

    checkin_str = fmt_date(b.get('arrival', ''))
    checkout_str = fmt_date(b.get('departure', ''))

    if not checkin_str:
        return None

    # 宿泊日数
    try:
        ci = date.fromisoformat(b['arrival'])
        co = date.fromisoformat(b['departure'])
        nights = (co - ci).days
    except Exception:
        nights = 0

    # 収入（ホスト受取額 = price - commission）
    price = b.get('price', 0) or 0
    commission = b.get('commission', 0) or 0
    revenue = price - commission

    # ゲスト名
    first = (b.get('firstName') or '').strip()
    last = (b.get('lastName') or '').strip()
    guest_name = f"{first} {last}".strip() or 'ゲスト'

    return {
        'id': b['id'],
        'source': source,
        'propId': prop_id,
        'guestName': guest_name,
        'checkin': checkin_str,
        'checkout': checkout_str,
        'nights': nights,
        'revenue': revenue,
        'price': price,
        'commission': commission,
        'code': str(b.get('apiReference', b['id'])),
        'channel': channel,
        'bookingTime': b.get('bookingTime', ''),
    }


# ==================== JavaScript生成 ====================

def generate_js(bookings_by_source: dict) -> str:
    """注入用JavaScriptを生成"""
    bc_bookings = bookings_by_source.get('booking', [])
    bc_json = json.dumps(bc_bookings, ensure_ascii=False)
    now_iso = date.today().isoformat() + 'T00:00:00.000Z'

    return f"""
// ===== Beds24 Booking.com データ注入 =====
(function() {{
  const newData = {bc_json};
  window._bookingComBookings = newData;
  bookingComBookings = newData;
  saveBookingComData();
  const now = "{now_iso}";
  dataUpdatedAt.bookingCom = now;
  saveDataUpdatedAt();
  updateHeaderTimestamp();
  renderAnnual();
  renderCalendarTab();
  renderMonthly();
  renderDashboard();
  console.log('[Beds24] Booking.com予約注入完了:', newData.length, '件');
}})();
""".strip()


# ==================== osascript注入 ====================

def inject_to_simulator(js_code: str) -> bool:
    """Apple Events JS経由でシミュレーターに注入"""
    escaped = js_code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    script = f'''tell application "Google Chrome"
  tell active tab of front window
    execute javascript "{escaped}"
  end tell
end tell'''
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"注入エラー: {result.stderr}", file=sys.stderr)
        return False
    return True


# ==================== メイン ====================

def main():
    print("🔑 Beds24 認証中...", file=sys.stderr)
    refresh_token = load_refresh_token()
    access_token = get_access_token(refresh_token)
    print("✅ 認証成功", file=sys.stderr)

    print("📥 予約データ取得中...", file=sys.stderr)
    raw_bookings = fetch_bookings(access_token)
    print(f"  取得件数（全チャネル）: {len(raw_bookings)}", file=sys.stderr)

    # 変換
    converted = [b for b in (convert_booking(r) for r in raw_bookings) if b]
    by_source: dict[str, list] = {}
    for b in converted:
        by_source.setdefault(b['source'], []).append(b)

    bc_count = len(by_source.get('booking', []))
    ab_count = len(by_source.get('airbnb', []))
    print(f"  Booking.com予約: {bc_count}件", file=sys.stderr)
    print(f"  Airbnb予約（Beds24経由・参考）: {ab_count}件", file=sys.stderr)

    # Booking.com予約のサマリー表示
    if bc_count > 0:
        print("\n📘 Booking.com予約一覧:", file=sys.stderr)
        for b in sorted(by_source['booking'], key=lambda x: x['checkin']):
            print(f"  {b['checkin']} → {b['checkout']} ({b['nights']}泊) {b['guestName']} ¥{b['revenue']:,}", file=sys.stderr)

    # JS生成
    js_code = generate_js(by_source)

    # osascriptで注入
    print("\n💉 シミュレーターに注入中...", file=sys.stderr)
    success = inject_to_simulator(js_code)
    if success:
        print("✅ 注入完了！", file=sys.stderr)
    else:
        print("⚠️ 注入失敗。以下のJSを手動でコンソールに貼り付けてください:", file=sys.stderr)
        print(js_code)


if __name__ == '__main__':
    main()
