# -*- coding: utf-8 -*-
"""
Render PostgreSQL -> Local SQLite migration script
"""
import sqlite3
import os
import sys
import shutil

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# PostgreSQL connection
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    print("DATABASE_URL 입력 (postgresql://...):")
    DATABASE_URL = input("> ").strip()

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

try:
    import psycopg2
    import psycopg2.extras
    pg = psycopg2.connect(DATABASE_URL)
    pg.autocommit = True
    pg_cur = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print("[OK] PostgreSQL 연결 성공")
except Exception as e:
    print(f"[ERR] PostgreSQL 연결 실패: {e}")
    sys.exit(1)

# SQLite connection
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facility.db')
if os.path.exists(db_path):
    backup = db_path.replace('.db', '_backup.db')
    shutil.copy2(db_path, backup)
    print(f"[OK] 기존 DB 백업: {backup}")

sq = sqlite3.connect(db_path)
sq_cur = sq.cursor()
print(f"[OK] SQLite: {db_path}")

SCHEMAS = {
    'users': '''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        employee_id TEXT UNIQUE NOT NULL,
        email TEXT DEFAULT '',
        phone TEXT NOT NULL,
        team TEXT NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT '점검자',
        is_admin INTEGER DEFAULT 0,
        is_approved INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'equipment': '''CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qr_code TEXT UNIQUE NOT NULL,
        location TEXT,
        department TEXT,
        description TEXT,
        approver_id INTEGER,
        created_by INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        inspection_cycle TEXT DEFAULT '매일',
        mgmt_no TEXT DEFAULT '',
        manager_primary TEXT DEFAULT '',
        manager_secondary TEXT DEFAULT ''
    )''',
    'inspections': '''CREATE TABLE IF NOT EXISTS inspections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL,
        inspector_id INTEGER NOT NULL,
        result TEXT NOT NULL,
        notes TEXT,
        status TEXT DEFAULT '점검완료',
        approved_by INTEGER,
        approved_at TEXT,
        inspected_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'inspection_templates': '''CREATE TABLE IF NOT EXISTS inspection_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER UNIQUE NOT NULL,
        filename TEXT,
        max_cols INTEGER DEFAULT 0,
        rows TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'inspection_details': '''CREATE TABLE IF NOT EXISTS inspection_details (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inspection_id INTEGER NOT NULL,
        row_index INTEGER NOT NULL,
        result TEXT NOT NULL DEFAULT '정상',
        detail_notes TEXT DEFAULT '',
        item_id INTEGER
    )''',
    'inspection_items': '''CREATE TABLE IF NOT EXISTS inspection_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL,
        item_order INTEGER DEFAULT 0,
        category TEXT DEFAULT '',
        item_name TEXT NOT NULL,
        criteria TEXT DEFAULT '',
        unit TEXT DEFAULT '',
        item_type TEXT DEFAULT '일반',
        min_val TEXT DEFAULT '',
        center_val TEXT DEFAULT '',
        max_val TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'monthly_notes': '''CREATE TABLE IF NOT EXISTS monthly_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL,
        year INTEGER NOT NULL,
        month INTEGER NOT NULL,
        notes TEXT DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(equipment_id, year, month)
    )''',
    'password_reset_requests': '''CREATE TABLE IF NOT EXISTS password_reset_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT '대기중',
        reset_code TEXT DEFAULT '',
        reset_expires TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'system_settings': '''CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    )''',
    'equipment_anomalies': '''CREATE TABLE IF NOT EXISTS equipment_anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL,
        inspection_id INTEGER,
        reporter_id INTEGER NOT NULL,
        occurred_at TEXT DEFAULT (datetime('now','localtime')),
        description TEXT NOT NULL,
        action_taken TEXT DEFAULT '',
        action_person TEXT DEFAULT '',
        priority TEXT DEFAULT '보통',
        planned_resolve_date TEXT DEFAULT '',
        is_resolved INTEGER DEFAULT 0,
        resolved_date TEXT DEFAULT '',
        resolved_at TEXT,
        resolved_by INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
    'anomaly_photos': '''CREATE TABLE IF NOT EXISTS anomaly_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anomaly_id INTEGER NOT NULL,
        photo_data TEXT NOT NULL,
        filename TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''',
}

TABLES = list(SCHEMAS.keys())

print("\n[1/3] 테이블 생성 중...")
for tbl, ddl in SCHEMAS.items():
    sq_cur.execute(ddl)
sq.commit()
print("[OK] 완료")

print("\n[2/3] 데이터 복사 중...")
total = 0
for tbl in TABLES:
    try:
        order = "ORDER BY id" if tbl != 'system_settings' else ""
        pg_cur.execute(f"SELECT * FROM {tbl} {order}")
        rows = pg_cur.fetchall()
    except Exception as e:
        print(f"  [SKIP] {tbl}: {e}")
        continue

    if not rows:
        print(f"  [--] {tbl}: 0건")
        continue

    cols = list(rows[0].keys())
    placeholders = ','.join(['?' for _ in cols])
    col_str = ','.join(cols)
    sq_cur.execute(f"DELETE FROM {tbl}")

    inserted = 0
    for row in rows:
        vals = [int(v) if isinstance(v, bool) else v for v in [row[c] for c in cols]]
        try:
            sq_cur.execute(f"INSERT OR REPLACE INTO {tbl} ({col_str}) VALUES ({placeholders})", vals)
            inserted += 1
        except Exception as e:
            print(f"    [WARN] {tbl} 삽입 오류: {e}")
    sq.commit()
    total += inserted
    print(f"  [OK] {tbl}: {inserted}건")

print(f"\n[OK] 총 {total}건 복사 완료")

print("\n[3/3] 검증...")
for tbl in TABLES:
    sq_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
    cnt = sq_cur.fetchone()[0]
    if cnt > 0:
        print(f"  {tbl}: {cnt}건")

pg.close()
sq.close()

print("\n" + "="*40)
print("이전 완료! facility.db 생성됨")
print("서버 실행: waitress-serve --host=0.0.0.0 --port=5000 --threads=4 app:app")
print("="*40)
