#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INTOPS 설비점검 시스템 - 자동 DB 백업 스크립트
실행: Windows 예약 작업으로 매일 새벽 2시 실행
저장 위치: //192.168.10.3/품질팀/QMS/AI/intops_backup/
"""

import os, csv, io, zipfile, sys
from datetime import datetime, timedelta

# 설정 영역 (필요시 수정하세요)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BACKUP_DIR   = r"\192.168.10.3\품질팀\■ QMS\AI\intops_backup"
KEEP_DAYS    = 30

TABLES = [
    ("users",     ["id","name","employee_id","email","phone","team","role","is_admin","is_approved","created_at"]),
    ("equipment", ["id","name","location","description","qr_code","approver_id","created_at"]),
    ("inspections",["id","equipment_id","user_id","status","inspected_at","approved_at","approver_id","note"]),
    ("inspection_items",["id","equipment_id","item_name","item_type","options","order_num"]),
    ("inspection_details",["id","inspection_id","item_id","value","note"]),
    ("monthly_notes",["id","equipment_id","year","month","note","updated_at"]),
]


def log(msg):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(os.path.join(BACKUP_DIR, "backup_log.txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_backup():
    log("시작: INTOPS DB 백업")

    if DATABASE_URL:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            def fetch(tbl, cols):
                cur = conn.cursor()
                cur.execute("SELECT " + ", ".join(cols) + " FROM " + tbl)
                return cur.fetchall()
            db_type = "PostgreSQL"
        except Exception as e:
            log(f"오류: PostgreSQL 연결 실패 - {e}")
            sys.exit(1)
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facility.db")
        conn = sqlite3.connect(db_path)
        def fetch(tbl, cols):
            cur = conn.cursor()
            cur.execute("SELECT " + ", ".join(cols) + " FROM " + tbl)
            return cur.fetchall()
        db_type = "SQLite (" + db_path + ")"

    log(f"DB 연결: {db_type}")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    now_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(BACKUP_DIR, "intops_backup_" + now_str + ".zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for tbl, cols in TABLES:
            try:
                rows = fetch(tbl, cols)
                buf  = io.StringIO()
                w    = csv.writer(buf)
                w.writerow(cols)
                w.writerows(rows)
                data = buf.getvalue().encode("utf-8-sig")
                zf.writestr(tbl + ".csv", data)
                log(f"  {tbl}: {len(rows)}건 저장")
            except Exception as e:
                log(f"  {tbl}: 오류 - {e}")
                zf.writestr(tbl + "_ERROR.txt", str(e))
        zf.writestr("_info.txt",
                    "INTOPS 백업 " + str(datetime.now()) + " / " + db_type)

    conn.close()
    log(f"완료: {zip_path}")

    # 오래된 백업 삭제
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    try:
        for fn in os.listdir(BACKUP_DIR):
            if fn.startswith("intops_backup_") and fn.endswith(".zip"):
                try:
                    d = datetime.strptime(fn[14:22], "%Y%m%d")
                    if d < cutoff:
                        os.remove(os.path.join(BACKUP_DIR, fn))
                        log(f"  오래된 백업 삭제: {fn}")
                except Exception:
                    pass
    except Exception:
        pass


if __name__ == "__main__":
    run_backup()
