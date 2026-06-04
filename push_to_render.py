# -*- coding: utf-8 -*-
"""
로컬 SQLite -> Render PostgreSQL HTTP 이전 스크립트
"""
import sqlite3, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import urllib.request, urllib.error
except ImportError:
    print("[ERR] urllib 없음"); sys.exit(1)

RENDER_URL = os.environ.get('RENDER_URL', 'https://intops-3.onrender.com')
SECRET     = os.environ.get('MIGRATE_SECRET', 'intops-migrate-2025')
ENDPOINT   = f'{RENDER_URL}/api/migrate-import'

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facility.db')
sq = sqlite3.connect(db_path)
sq.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
cur = sq.cursor()

TABLES = [
    'users', 'equipment', 'inspections',
    'inspection_templates', 'inspection_details', 'inspection_items',
    'monthly_notes', 'password_reset_requests', 'system_settings',
    'equipment_anomalies', 'anomaly_photos',
]

CHUNK = 200  # 한 번에 전송할 행 수

def post(payload):
    body = json.dumps(payload, default=str, ensure_ascii=False).encode('utf-8')
    req  = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'X-Migrate-Secret': SECRET,
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': f'HTTP {e.code}: {e.read().decode()}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

print(f"[접속] {ENDPOINT}")
total = 0

for tbl in TABLES:
    cur.execute(f"SELECT * FROM {tbl}")
    rows = cur.fetchall()
    print(f"\n[{tbl}] {len(rows)}건 전송 중...")

    if not rows:
        # 빈 테이블도 truncate
        res = post({'table': tbl, 'rows': [], 'truncate': True})
        print(f"  [OK] 비움")
        continue

    # 첫 청크에만 truncate
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        is_last = (i + CHUNK) >= len(rows)
        payload = {
            'table':     tbl,
            'rows':      chunk,
            'truncate':  (i == 0),
            'reset_seq': is_last and tbl != 'system_settings',
        }
        res = post(payload)
        if res.get('ok'):
            inserted = res.get('inserted', len(chunk))
            total += inserted
            print(f"  [{i+1}~{i+len(chunk)}] {inserted}건 ✓")
        else:
            print(f"  [ERR] {res.get('error')}")
            break

sq.close()
print(f"\n{'='*40}")
print(f"이전 완료! 총 {total}건")
print(f"{'='*40}")
print(f"Render 서버 확인: {RENDER_URL}/login")
