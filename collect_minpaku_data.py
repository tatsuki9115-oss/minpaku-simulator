#!/usr/bin/env python3
from __future__ import annotations
"""
民泊チェック用データ収集スクリプト（Macで実行）
使い方: python3 collect_minpaku_data.py
       python3 collect_minpaku_data.py --no-prompt   # 手入力プロンプトを省略

1コマンドで以下を1フォルダに集める:
  1. Beds24 API      : 全チャネル予約 + 日別カレンダー（価格・min stay・在庫）
  2. シミュレーター   : 開いているタブで downloadBackup() を実行しJSONを回収
  3. Airbnb          : インサイト/パフォーマンス画面のテキストを開いているタブから取得
                       （閲覧数・成約率。取れなければ手入力プロンプト）
  4. Booking.com     : extranet 画面のテキストを開いているタブから取得
                       （閲覧数。取れなければ手入力プロンプト）
  ※ PriceLabs はチェック時に Claude が MCP で直接取得するためここでは集めない

出力フォルダを Google Drive の「民泊バックアップ」に置けば、
Claude が次回の民泊チェックで自動的に読み込む。
（Google Drive デスクトップが入っていれば自動コピーを試みる）
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

# ==================== 設定 ====================

TOKEN_FILE = Path.home() / '.beds24_refresh_token'

# Beds24 propertyId → シミュレーターのpropId / 表示名
PROPERTY_MAP = {
    327669: {'propId': 1, 'name': 'ATTビル（高幡不動）'},
    328081: {'propId': 2, 'name': 'フィオーレ新子安'},
}

CALENDAR_DAYS_AHEAD = 120   # カレンダーを何日先まで取るか
BOOKINGS_FROM = '2026-01-01'  # 予約をいつのチェックアウト分から取るか

DOWNLOADS = Path.home() / 'Downloads'

# ==================== 共通 ====================

def log(msg: str):
    print(msg, file=sys.stderr)


def osascript(script: str) -> tuple[bool, str]:
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or '').strip() if r.returncode == 0 else (r.stderr or '').strip()


def chrome_tab_js(url_substrings: list[str], js: str) -> str | None:
    """URLに部分一致する最初のChromeタブでJSを実行し結果を返す。見つからなければNone"""
    escaped = js.replace('\\', '\\\\').replace('"', '\\"')
    conds = ' or '.join(f'URL of t contains "{s}"' for s in url_substrings)
    script = f'''tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if {conds} then
        return execute t javascript "{escaped}"
      end if
    end repeat
  end repeat
  return "__TAB_NOT_FOUND__"
end tell'''
    ok, out = osascript(script)
    if not ok or out == '__TAB_NOT_FOUND__':
        return None
    return out


# ==================== 1. Beds24 ====================

def beds24_token() -> str | None:
    if not TOKEN_FILE.exists():
        log(f'  ⚠️ リフレッシュトークンがありません: {TOKEN_FILE}')
        return None
    refresh = TOKEN_FILE.read_text().strip()
    try:
        req = urllib.request.Request(
            'https://api.beds24.com/v2/authentication/token',
            headers={'refreshToken': refresh})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get('token')
    except Exception as e:
        log(f'  ⚠️ Beds24認証失敗: {e}')
        return None


def beds24_get(token: str, path_and_query: str) -> dict | None:
    try:
        req = urllib.request.Request(
            f'https://api.beds24.com/v2{path_and_query}', headers={'token': token})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f'  ⚠️ Beds24 GET {path_and_query} 失敗: {e}')
        return None


def collect_beds24(outdir: Path, manifest: dict):
    log('📥 [1/4] Beds24 ...')
    token = beds24_token()
    if not token:
        manifest['beds24'] = {'status': 'FAILED', 'reason': 'auth'}
        return

    # 予約（全チャネル・ページング）
    all_bookings = []
    page = 1
    while True:
        data = beds24_get(token, f'/bookings?limit=200&page={page}&departureFrom={BOOKINGS_FROM}')
        if not data:
            break
        rows = data.get('data', [])
        all_bookings.extend(rows)
        if not data.get('pages', {}).get('nextPageExists') or len(rows) == 0:
            break
        page += 1
    (outdir / 'beds24_bookings.json').write_text(
        json.dumps(all_bookings, ensure_ascii=False, indent=1))
    log(f'  予約 {len(all_bookings)}件（全チャネル）')

    # 物件情報
    props = beds24_get(token, '/properties?includeAllRooms=true')
    if props:
        (outdir / 'beds24_properties.json').write_text(
            json.dumps(props.get('data', props), ensure_ascii=False, indent=1))

    # 日別カレンダー（価格・min stay・在庫）
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=CALENDAR_DAYS_AHEAD)).isoformat()
    calendars = {}
    for pid in PROPERTY_MAP:
        cal = beds24_get(
            token,
            f'/inventory/rooms/calendar?propertyId={pid}&startDate={start}&endDate={end}'
            f'&includePrices=true&includeMinStay=true&includeMaxStay=true&includeNumAvail=true')
        if cal:
            calendars[str(pid)] = cal.get('data', cal)
    if calendars:
        (outdir / 'beds24_calendar.json').write_text(
            json.dumps(calendars, ensure_ascii=False, indent=1))
        log(f'  カレンダー {start}〜{end}（価格・minStay・在庫）')

    manifest['beds24'] = {
        'status': 'OK' if all_bookings else 'PARTIAL',
        'bookings': len(all_bookings),
        'calendar': bool(calendars),
        'range': [BOOKINGS_FROM, end],
    }


# ==================== 2. シミュレーター ====================

def collect_simulator(outdir: Path, manifest: dict):
    log('📥 [2/4] 民泊シミュレーター ...')
    before = set(glob.glob(str(DOWNLOADS / 'minpaku_backup_*.json')))
    result = chrome_tab_js(
        ['minpaku-simulator', 'netlify.app'],
        'downloadBackup(); "ok"')
    if result is None:
        log('  ⚠️ シミュレーターのタブが見つかりません。ブラウザで開いてから再実行してください。')
        manifest['simulator'] = {'status': 'FAILED', 'reason': 'tab_not_found'}
        return
    # ダウンロード完了を待って新しいファイルを回収
    newfile = None
    for _ in range(20):
        time.sleep(0.5)
        now = set(glob.glob(str(DOWNLOADS / 'minpaku_backup_*.json')))
        added = now - before
        if added:
            newfile = max(added, key=os.path.getmtime)
            break
    if not newfile:
        # 新規が出なければ既存の最新を使う（自動バックアップ済みの場合）
        existing = sorted(before, key=os.path.getmtime)
        newfile = existing[-1] if existing else None
        if newfile:
            log(f'  ℹ️ 新規DLを検出できず、既存の最新を使用: {Path(newfile).name}')
    if not newfile:
        manifest['simulator'] = {'status': 'FAILED', 'reason': 'download_not_found'}
        return
    dest = outdir / 'simulator_backup.json'
    shutil.copy2(newfile, dest)
    try:
        data = json.loads(dest.read_text())
        counts = {
            'airbnbBookings': len(data.get('airbnbBookings', [])),
            'bookingComBookings': len(data.get('bookingComBookings', [])),
            'monthlyData_months': len(data.get('monthlyData', {})),
            'exportedAt': data.get('exportedAt'),
        }
    except Exception:
        counts = {}
    log(f'  {Path(newfile).name} を回収 {counts}')
    manifest['simulator'] = {'status': 'OK', **counts}


# ==================== 3/4. Airbnb・Booking.com 画面テキスト ====================

INNER_TEXT_JS = 'document.title + "\\n=====\\n" + location.href + "\\n=====\\n" + document.body.innerText'


def collect_page_text(outdir: Path, manifest: dict, key: str, label: str,
                      url_parts: list[str], out_name: str, open_url: str,
                      prompts: list[tuple[str, str]], no_prompt: bool):
    log(f'📥 {label} ...')
    text = chrome_tab_js(url_parts, INNER_TEXT_JS)
    entry = {}
    if text and len(text) > 200:
        (outdir / out_name).write_text(text)
        log(f'  画面テキストを取得（{len(text)}文字）→ {out_name}')
        entry = {'status': 'OK', 'method': 'tab_text', 'chars': len(text)}
    else:
        log(f'  ⚠️ 対象タブが見つかりません。参考: {open_url}')
        entry = {'status': 'MISSING', 'hint': open_url}
        if not no_prompt:
            manual = {}
            log('  数値を手入力できます（不明なら Enter でスキップ）')
            for field, question in prompts:
                try:
                    v = input(f'    {question}: ').strip()
                except EOFError:
                    v = ''
                if v:
                    manual[field] = v
            if manual:
                (outdir / out_name.replace('_raw.txt', '_manual.json')).write_text(
                    json.dumps(manual, ensure_ascii=False, indent=1))
                entry = {'status': 'MANUAL', 'fields': list(manual.keys())}
    manifest[key] = entry


# ==================== メイン ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-prompt', action='store_true', help='手入力プロンプトを出さない')
    args = ap.parse_args()

    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    outdir = DOWNLOADS / f'minpaku_check_{stamp}'
    outdir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {'collectedAt': datetime.now().isoformat(), 'folder': outdir.name}

    collect_beds24(outdir, manifest)
    collect_simulator(outdir, manifest)
    collect_page_text(
        outdir, manifest, 'airbnb_insights', '[3/4] Airbnb インサイト（閲覧数・成約率）',
        ['airbnb.com/hosting', 'airbnb.jp/hosting'],
        'airbnb_insights_raw.txt',
        'https://www.airbnb.jp/hosting/insights',
        [('views_30d_takahata', '高幡不動: 過去30日の表示回数'),
         ('conversion_takahata', '高幡不動: 成約率(%)'),
         ('views_30d_shinkoyasu', '新子安: 過去30日の表示回数'),
         ('conversion_shinkoyasu', '新子安: 成約率(%)')],
        args.no_prompt)
    collect_page_text(
        outdir, manifest, 'bcom_extranet', '[4/4] Booking.com extranet（閲覧数）',
        ['admin.booking.com'],
        'bcom_extranet_raw.txt',
        'https://admin.booking.com/ → アナリティクス → 掲載ページの閲覧数',
        [('views_30d_shinkoyasu', '新子安: 過去30日の閲覧数')],
        args.no_prompt)

    (outdir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))

    # Google Drive デスクトップがあれば「民泊バックアップ」へ自動コピー
    drive_dst = None
    for pat in [
        str(Path.home() / 'Library/CloudStorage/GoogleDrive-*/マイドライブ/民泊バックアップ'),
        str(Path.home() / 'Library/CloudStorage/GoogleDrive-*/My Drive/民泊バックアップ'),
        str(Path.home() / 'Google Drive/民泊バックアップ'),
    ]:
        hits = glob.glob(pat)
        if hits:
            drive_dst = Path(hits[0]) / outdir.name
            break
    if drive_dst:
        shutil.copytree(outdir, drive_dst, dirs_exist_ok=True)
        log(f'\n✅ 完了: Google Drive にコピーしました → 民泊バックアップ/{outdir.name}')
    else:
        log(f'\n✅ 完了: {outdir}')
        log('   ⚠️ このフォルダを Google Drive の「民泊バックアップ」にアップロードしてください。')
        log('   （それで Claude が次回の民泊チェックで読み込めます）')

    # サマリー
    log('\n--- 収集結果 ---')
    for k, v in manifest.items():
        if isinstance(v, dict) and 'status' in v:
            mark = {'OK': '✅', 'PARTIAL': '🟡', 'MANUAL': '🟡', 'MISSING': '❌', 'FAILED': '❌'}.get(v['status'], '❓')
            log(f'  {mark} {k}: {v["status"]}')


if __name__ == '__main__':
    main()
