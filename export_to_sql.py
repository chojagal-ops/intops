# -*- coding: utf-8 -*-
"""
SQLite -> SQL INSERT 파일 생성 (Supabase SQL Editor에서 실행)
"""
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

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

def escape(v):
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    # 문자열: 작은따옴표 이스케이프
    return "'" + str(v).replace("'", "''") + "'"

out_dir = os.path.dirname(os.path.abspath(__file__))
all_sql_path = os.path.join(out_dir, 'migration_all.sql')

with open(all_sql_path, 'w', encoding='utf-8') as fall:
    fall.write('-- INTOPS 전체 데이터 이전 SQL\n')
    fall.write('-- Supabase SQL Editor에서 실행하세요\n\n')

    for tbl in TABLES:
        cur.execute(f"SELECT * FROM {tbl}")
        rows = cur.fetchall()

        # 테이블별 파일도 별도 생성
        tbl_path = os.path.join(out_dir, f'migration_{tbl}.sql')
        with open(tbl_path, 'w', encoding='utf-8') as f:
            f.write(f'-- {tbl} ({len(rows)}건)\n')
            f.write(f'DELETE FROM {tbl};\n\n')

            if rows:
                cols = list(rows[0].keys())
                col_str = ', '.join(f'"{c}"' for c in cols)

                for row in rows:
                    vals = ', '.join(escape(row[c]) for c in cols)
                    sql = f'INSERT INTO {tbl} ({col_str}) VALUES ({vals});\n'
                    f.write(sql)

            # 시퀀스 재설정 (system_settings 제외)
            if tbl != 'system_settings' and rows:
                max_id = max(r['id'] for r in rows if 'id' in r)
                f.write(f"\nSELECT setval(pg_get_serial_sequence('{tbl}','id'), {max_id});\n")

        # 전체 파일에도 추가
        with open(tbl_path, 'r', encoding='utf-8') as f:
            fall.write(f.read())
            fall.write('\n\n')

        print(f"[OK] {tbl}: {len(rows)}건 -> migration_{tbl}.sql")

sq.close()
print(f"\n[완료] 전체 파일: migration_all.sql")
print(f"[완료] 테이블별 파일: migration_각테이블.sql")
print(f"\n저장 위치: {out_dir}")
