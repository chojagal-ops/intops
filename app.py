from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import hashlib
import uuid
import threading
import smtplib
import json
import os
import calendar
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
from functools import wraps

try:
    import email_config
except ImportError:
    class email_config:
        ENABLED = False
        SMTP_SERVER = 'smtp.gmail.com'
        SMTP_PORT = 587
        SENDER_EMAIL = ''
        SENDER_PASSWORD = ''

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

# ── DB 백엔드 선택 ─────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

# SQL 방언 상수
_PK           = 'SERIAL PRIMARY KEY'          if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
_NOW_DEFAULT  = "to_char(NOW() AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD HH24:MI:SS')" \
                if USE_PG else "datetime('now','localtime')"
TODAY         = 'CURRENT_DATE'                if USE_PG else "date('now','localtime')"
NOW_FN        = 'NOW()'                       if USE_PG else "datetime('now','localtime')"

def date_col(col):
    return f"DATE({col} AT TIME ZONE 'Asia/Seoul')" if USE_PG else f"date({col})"


# ── DB 연결 래퍼 ──────────────────────────────────────────────────────────────
class _PGRow(dict):
    """dict 를 sqlite3.Row 처럼 속성 접근도 지원하도록 확장"""
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


class _PGCursorWrapper:
    def __init__(self, cur):
        self._c = cur

    def _wrap(self, row):
        return _PGRow(row) if row is not None else None

    def fetchall(self):
        return [_PGRow(r) for r in self._c.fetchall()]

    def fetchone(self):
        row = self._c.fetchone()
        return _PGRow(row) if row else None

    def __iter__(self):
        return iter(self.fetchall())


class DBConn:
    def __init__(self):
        if USE_PG:
            self._conn = psycopg2.connect(DATABASE_URL)
        else:
            self._conn = sqlite3.connect('facility.db')
            self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        if USE_PG:
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql.replace('?', '%s'), params or None)
            return _PGCursorWrapper(cur)
        return self._conn.execute(sql, params)

    def insert(self, sql, params=()):
        """INSERT 후 새 row ID 반환"""
        if USE_PG:
            cur = self._conn.cursor()
            pg_sql = (sql + ' RETURNING id').replace('?', '%s')
            cur.execute(pg_sql, params or None)
            return cur.fetchone()[0]
        cur = self._conn.execute(sql, params)
        return cur.lastrowid

    def commit(self): self._conn.commit()
    def close(self):  self._conn.close()


def get_db():
    return DBConn()


# ── Flask 앱 ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'intops_facility_2024_secret')

TEAMS = ['품질팀', 'EMS제조팀', '생산기술팀', '개발팀', '환경안전팀', '관리자']


# ── 엑셀 파싱 ─────────────────────────────────────────────────────────────────
def parse_excel(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    rows_data = []
    max_cols  = 0
    for row in ws.iter_rows(values_only=True):
        cells = []
        for c in row:
            if c is None:
                cells.append('')
            elif isinstance(c, float) and c == int(c):
                cells.append(str(int(c)))
            else:
                cells.append(str(c).strip())
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        max_cols = max(max_cols, len(cells))
        try:
            int(float(cells[0]))
            is_item = True
        except (ValueError, TypeError, IndexError):
            is_item = False
        rows_data.append({'cells': cells, 'is_item': is_item})
    return rows_data, max_cols


# ── 이메일 발송 ───────────────────────────────────────────────────────────────
def _build_email_html(approver_name, inspector_name, eq_name, location,
                      result, notes, inspect_url):
    result_color = {
        '정상': '#16a34a', '이상': '#f97316',
        '수리필요': '#dc2626', '휴동': '#6b7280',
    }.get(result, '#f97316')
    result_icon = {'정상': '✅', '이상': '⚠️', '수리필요': '🔴', '휴동': '⏸'}.get(result, '📋')
    notes_row = f'''
        <tr>
          <td style="padding:8px 0;color:#6b7280;width:100px;">특이사항</td>
          <td style="padding:8px 0;color:#111827;">{notes}</td>
        </tr>''' if notes else ''

    return f'''<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:'Malgun Gothic',sans-serif;">
  <div style="max-width:520px;margin:32px auto;background:#fff;
              border-radius:16px;overflow:hidden;border:1px solid #e5e7eb;">
    <div style="background:linear-gradient(135deg,#f97316,#ea580c);
                padding:28px 32px;text-align:center;">
      <div style="color:#fff;font-size:1.2rem;font-weight:700;letter-spacing:1px;">
        INTOPS 설비점검 시스템
      </div>
      <div style="color:rgba(255,255,255,0.85);font-size:0.88rem;margin-top:4px;">
        점검 승인 요청 알림
      </div>
    </div>
    <div style="padding:32px;">
      <p style="color:#111827;font-size:1rem;margin:0 0 24px;">
        안녕하세요, <strong style="color:#f97316;">{approver_name}</strong> 님.<br>
        아래 설비에 대한 점검이 완료되어 승인 요청이 접수되었습니다.
      </p>
      <div style="background:#f8f9fa;border-radius:10px;padding:20px 24px;
                  border:1px solid #e5e7eb;margin-bottom:24px;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="padding:8px 0;color:#6b7280;width:100px;">설비명</td>
              <td style="padding:8px 0;color:#111827;font-weight:700;">{eq_name}</td></tr>
          <tr><td style="padding:8px 0;color:#6b7280;">설치 위치</td>
              <td style="padding:8px 0;color:#111827;">{location}</td></tr>
          <tr><td style="padding:8px 0;color:#6b7280;">점검자</td>
              <td style="padding:8px 0;color:#111827;">{inspector_name}</td></tr>
          <tr><td style="padding:8px 0;color:#6b7280;">점검 결과</td>
              <td style="padding:8px 0;">
                <span style="background:{result_color}18;color:{result_color};
                             padding:3px 12px;border-radius:8px;font-weight:700;">
                  {result_icon} {result}
                </span>
              </td></tr>
          {notes_row}
        </table>
      </div>
      <div style="text-align:center;margin-bottom:24px;">
        <a href="{inspect_url}"
           style="display:inline-block;background:linear-gradient(135deg,#f97316,#ea580c);
                  color:#fff;text-decoration:none;padding:14px 36px;
                  border-radius:10px;font-weight:700;">
          승인 확인하기 →
        </a>
      </div>
      <p style="color:#9ca3af;font-size:0.8rem;text-align:center;margin:0;">
        이 메일은 INTOPS 설비점검 시스템에서 자동 발송된 메일입니다.
      </p>
    </div>
  </div>
</body></html>'''


def _send_mail(to_email, subject, html_body):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = email_config.SENDER_EMAIL
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(email_config.SMTP_SERVER, email_config.SMTP_PORT) as s:
            s.starttls()
            s.login(email_config.SENDER_EMAIL, email_config.SENDER_PASSWORD)
            s.sendmail(email_config.SENDER_EMAIL, to_email, msg.as_string())
        print(f'[이메일] 발송 완료 → {to_email}')
    except Exception as e:
        print(f'[이메일] 발송 실패: {e}')


def send_approval_request(to_email, approver_name, inspector_name,
                          eq_name, location, result, notes, eq_id, host_url):
    if not email_config.ENABLED or not to_email:
        return
    inspect_url = host_url.rstrip('/') + url_for('inspect', eq_id=eq_id)
    subject     = f'[설비점검] 승인 요청 - {eq_name}'
    html_body   = _build_email_html(approver_name, inspector_name,
                                    eq_name, location, result, notes, inspect_url)
    t = threading.Thread(target=_send_mail, args=(to_email, subject, html_body), daemon=True)
    t.start()


# ── DB 초기화 ─────────────────────────────────────────────────────────────────
def init_db():
    conn = get_db()

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id          {_PK},
            name        TEXT NOT NULL,
            employee_id TEXT UNIQUE NOT NULL,
            email       TEXT DEFAULT '',
            phone       TEXT NOT NULL,
            team        TEXT NOT NULL,
            password    TEXT NOT NULL,
            role        TEXT DEFAULT '점검자',
            is_admin    INTEGER DEFAULT 0,
            is_approved INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT ({_NOW_DEFAULT})
        )
    ''')

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS equipment (
            id          {_PK},
            name        TEXT NOT NULL,
            qr_code     TEXT UNIQUE NOT NULL,
            location    TEXT,
            department  TEXT,
            description TEXT,
            approver_id INTEGER,
            created_by  INTEGER,
            created_at  TEXT DEFAULT ({_NOW_DEFAULT})
        )
    ''')

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS inspections (
            id            {_PK},
            equipment_id  INTEGER NOT NULL,
            inspector_id  INTEGER NOT NULL,
            result        TEXT NOT NULL,
            notes         TEXT,
            status        TEXT DEFAULT '점검완료',
            approved_by   INTEGER,
            approved_at   TEXT,
            inspected_at  TEXT DEFAULT ({_NOW_DEFAULT})
        )
    ''')

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS inspection_templates (
            id           {_PK},
            equipment_id INTEGER UNIQUE NOT NULL,
            filename     TEXT,
            max_cols     INTEGER DEFAULT 0,
            rows         TEXT,
            created_at   TEXT DEFAULT ({_NOW_DEFAULT})
        )
    ''')

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS inspection_details (
            id            {_PK},
            inspection_id INTEGER NOT NULL,
            row_index     INTEGER NOT NULL,
            result        TEXT NOT NULL DEFAULT '정상',
            detail_notes  TEXT DEFAULT ''
        )
    ''')

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS inspection_items (
            id           {_PK},
            equipment_id INTEGER NOT NULL,
            item_order   INTEGER DEFAULT 0,
            category     TEXT DEFAULT '',
            item_name    TEXT NOT NULL,
            criteria     TEXT DEFAULT '',
            unit         TEXT DEFAULT '',
            created_at   TEXT DEFAULT ({_NOW_DEFAULT})
        )
    ''')

    # 마이그레이션
    if USE_PG:
        migrations = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT '점검자'",
            "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS approver_id INTEGER",
            "ALTER TABLE inspections ADD COLUMN IF NOT EXISTS status TEXT DEFAULT '점검완료'",
            "ALTER TABLE inspections ADD COLUMN IF NOT EXISTS approved_by INTEGER",
            "ALTER TABLE inspections ADD COLUMN IF NOT EXISTS approved_at TEXT",
            "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS inspection_cycle TEXT DEFAULT '매일'",
            "ALTER TABLE inspection_details ADD COLUMN IF NOT EXISTS item_id INTEGER",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                conn._conn.rollback()
    else:
        migrations = [
            "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN role TEXT DEFAULT '점검자'",
            "ALTER TABLE equipment ADD COLUMN approver_id INTEGER",
            "ALTER TABLE inspections ADD COLUMN status TEXT DEFAULT '점검완료'",
            "ALTER TABLE inspections ADD COLUMN approved_by INTEGER",
            "ALTER TABLE inspections ADD COLUMN approved_at TEXT",
            "ALTER TABLE equipment ADD COLUMN inspection_cycle TEXT DEFAULT '매일'",
            "ALTER TABLE inspection_details ADD COLUMN item_id INTEGER",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass

    admin_pw = hashlib.sha256('admin123'.encode()).hexdigest()

    if USE_PG:
        conn.execute('''
            INSERT INTO users (name, employee_id, email, phone, team, password, role, is_admin, is_approved)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
            ON CONFLICT (employee_id) DO NOTHING
        ''', ('관리자', 'admin', 'admin@company.com', '010-0000-0000', '경영지원그룹', admin_pw, '승인자'))
    else:
        conn.execute('''
            INSERT OR IGNORE INTO users
                (name, employee_id, email, phone, team, password, role, is_admin, is_approved)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
        ''', ('관리자', 'admin', 'admin@company.com', '010-0000-0000', '경영지원그룹', admin_pw, '승인자'))

    conn.commit()
    conn.close()


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


# gunicorn 포함 모든 실행 환경에서 DB 초기화 보장
with app.app_context():
    init_db()


# ── 데코레이터 ────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('관리자 권한이 필요합니다.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


# ── 스플래시 ──────────────────────────────────────────────────────────────────
@app.route('/')
def splash():
    return render_template('splash.html')


# ── 로그인 ────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next', '')
    if request.method == 'POST':
        emp_id   = request.form['employee_id'].strip()
        password = hash_pw(request.form['password'])
        next_url = request.form.get('next', '')
        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE employee_id=? AND password=?',
            (emp_id, password)
        ).fetchone()
        conn.close()
        if user is None:
            flash('사번 또는 비밀번호가 올바르지 않습니다.', 'error')
        elif not user['is_approved']:
            flash('관리자 승인 대기 중입니다.', 'warning')
        else:
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['is_admin']  = bool(user['is_admin'])
            session['role']      = user['role'] or '점검자'
            if next_url:
                return redirect(next_url)
            if user['is_admin']:
                return redirect(url_for('admin'))
            return redirect(url_for('dashboard'))
    return render_template('login.html', next_url=next_url)


# ── 회원가입 ──────────────────────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name   = request.form['name'].strip()
        emp_id = request.form['employee_id'].strip()
        email  = request.form['email'].strip()
        phone  = request.form['phone'].strip()
        team   = request.form['team'].strip()
        pw     = request.form['password']
        pw_cfm = request.form['password_confirm']
        if pw != pw_cfm:
            flash('비밀번호가 일치하지 않습니다.', 'error')
            return render_template('register.html', teams=TEAMS)
        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO users (name, employee_id, email, phone, team, password) VALUES (?,?,?,?,?,?)',
                (name, emp_id, email, phone, team, hash_pw(pw))
            )
            conn.commit()
            flash('회원가입 신청이 완료되었습니다. 관리자 승인 후 로그인 가능합니다.', 'success')
            return redirect(url_for('login'))
        except Exception:
            flash('이미 사용 중인 사번입니다.', 'error')
        finally:
            conn.close()
    return render_template('register.html', teams=TEAMS)


# ── 관리자: 회원 승인 ─────────────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin():
    conn = get_db()
    pending  = conn.execute('SELECT * FROM users WHERE is_approved=0 ORDER BY created_at DESC').fetchall()
    approved = conn.execute('SELECT * FROM users WHERE is_approved=1 AND is_admin=0 ORDER BY role, created_at DESC').fetchall()
    conn.close()
    return render_template('admin.html', pending=pending, approved=approved)


@app.route('/admin/approve/<int:user_id>', methods=['POST'])
@admin_required
def approve(user_id):
    role = request.form.get('role', '점검자')
    if role not in ('점검자', '승인자'):
        role = '점검자'
    conn = get_db()
    conn.execute('UPDATE users SET is_approved=1, role=? WHERE id=?', (role, user_id))
    conn.commit()
    conn.close()
    flash(f'{role}로 승인되었습니다.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/reject/<int:user_id>')
@admin_required
def reject(user_id):
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    conn.commit()
    conn.close()
    flash('신청이 거부되었습니다.', 'info')
    return redirect(url_for('admin'))


@app.route('/admin/change-role/<int:user_id>', methods=['POST'])
@admin_required
def change_role(user_id):
    role = request.form.get('role', '점검자')
    conn = get_db()
    conn.execute('UPDATE users SET role=? WHERE id=?', (role, user_id))
    conn.commit()
    conn.close()
    flash('역할이 변경되었습니다.', 'success')
    return redirect(url_for('admin'))


# ── 관리자: 설비 관리 ─────────────────────────────────────────────────────────
@app.route('/admin/equipment')
@admin_required
def admin_equipment():
    conn = get_db()
    equipments = conn.execute('''
        SELECT e.*,
               u.name  AS creator_name,
               a.name  AS approver_name,
               COUNT(DISTINCT i.id) AS inspection_count,
               t.id       AS template_id,
               t.filename AS template_file
        FROM equipment e
        LEFT JOIN users u ON e.created_by  = u.id
        LEFT JOIN users a ON e.approver_id = a.id
        LEFT JOIN inspections i ON e.id = i.equipment_id
        LEFT JOIN inspection_templates t ON e.id = t.equipment_id
        GROUP BY e.id, u.name, a.name, t.id, t.filename
        ORDER BY e.created_at DESC
    ''').fetchall()
    approvers = conn.execute(
        "SELECT id, name, team FROM users WHERE (role='승인자' OR is_admin=1) AND is_approved=1 ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template('admin_equipment.html', equipments=equipments,
                           approvers=approvers, has_qrcode=HAS_QRCODE,
                           has_openpyxl=HAS_OPENPYXL)


@app.route('/admin/equipment/set-approver/<int:eq_id>', methods=['POST'])
@admin_required
def set_equipment_approver(eq_id):
    approver_id = request.form.get('approver_id') or None
    conn = get_db()
    conn.execute('UPDATE equipment SET approver_id=? WHERE id=?', (approver_id, eq_id))
    conn.commit()
    conn.close()
    flash('승인자가 지정되었습니다.', 'success')
    return redirect(url_for('admin_equipment'))


@app.route('/admin/equipment/add', methods=['GET', 'POST'])
@admin_required
def admin_equipment_add():
    conn = get_db()
    approvers = conn.execute(
        "SELECT id, name, team FROM users WHERE (role='승인자' OR is_admin=1) AND is_approved=1 ORDER BY name"
    ).fetchall()
    conn.close()
    if request.method == 'POST':
        name        = request.form['name'].strip()
        location    = request.form['location'].strip()
        department  = request.form['department'].strip()
        description = request.form.get('description', '').strip()
        approver_id = request.form.get('approver_id') or None
        qr_code     = request.form.get('qr_code', '').strip() or str(uuid.uuid4())
        conn = get_db()
        try:
            inspection_cycle = request.form.get('inspection_cycle', '매일')
            eq_id_new = conn.insert(
                '''INSERT INTO equipment
                   (name, qr_code, location, department, description, approver_id, created_by, inspection_cycle)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (name, qr_code, location, department, description, approver_id, session['user_id'], inspection_cycle)
            )
            # 점검 항목 저장
            item_names      = request.form.getlist('item_name')
            item_categories = request.form.getlist('item_category')
            item_criterias  = request.form.getlist('item_criteria')
            item_units      = request.form.getlist('item_unit')
            for i, iname in enumerate(item_names):
                if iname.strip():
                    cat = item_categories[i] if i < len(item_categories) else ''
                    cri = item_criterias[i]  if i < len(item_criterias)  else ''
                    unt = item_units[i]      if i < len(item_units)       else ''
                    conn.execute(
                        'INSERT INTO inspection_items (equipment_id, item_order, category, item_name, criteria, unit) VALUES (?,?,?,?,?,?)',
                        (eq_id_new, i+1, cat.strip(), iname.strip(), cri.strip(), unt.strip())
                    )
            conn.commit()
            flash(f'설비 "{name}" 이(가) 등록되었습니다.', 'success')
            return redirect(url_for('admin_equipment'))
        except Exception:
            flash('이미 등록된 QR 코드입니다.', 'error')
        finally:
            conn.close()
    return render_template('admin_equipment_add.html', teams=TEAMS, approvers=approvers)


@app.route('/admin/equipment/upload-template/<int:eq_id>', methods=['POST'])
@admin_required
def upload_template(eq_id):
    if not HAS_OPENPYXL:
        flash('openpyxl 패키지 필요: pip install openpyxl', 'error')
        return redirect(url_for('admin_equipment'))
    file = request.files.get('excel_file')
    if not file or not file.filename:
        flash('파일을 선택해주세요.', 'error')
        return redirect(url_for('admin_equipment'))
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        flash('Excel 파일(.xlsx)만 업로드 가능합니다.', 'error')
        return redirect(url_for('admin_equipment'))
    try:
        rows_data, max_cols = parse_excel(file)
    except Exception as e:
        flash(f'파일 읽기 실패: {e}', 'error')
        return redirect(url_for('admin_equipment'))
    if not rows_data:
        flash('Excel에서 데이터를 찾을 수 없습니다.', 'error')
        return redirect(url_for('admin_equipment'))

    conn = get_db()
    if USE_PG:
        conn.execute('''
            INSERT INTO inspection_templates (equipment_id, filename, max_cols, rows)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (equipment_id) DO UPDATE SET
                filename = EXCLUDED.filename,
                max_cols = EXCLUDED.max_cols,
                rows     = EXCLUDED.rows
        ''', (eq_id, file.filename, max_cols, json.dumps(rows_data, ensure_ascii=False)))
    else:
        conn.execute('''
            INSERT OR REPLACE INTO inspection_templates
                (equipment_id, filename, max_cols, rows)
            VALUES (?, ?, ?, ?)
        ''', (eq_id, file.filename, max_cols, json.dumps(rows_data, ensure_ascii=False)))
    conn.commit()
    conn.close()
    item_count = sum(1 for r in rows_data if r['is_item'])
    flash(f'점검표가 등록되었습니다. (점검 항목 {item_count}개 감지)', 'success')
    return redirect(url_for('admin_equipment'))


@app.route('/admin/equipment/delete-template/<int:eq_id>')
@admin_required
def delete_template(eq_id):
    conn = get_db()
    conn.execute('DELETE FROM inspection_templates WHERE equipment_id=?', (eq_id,))
    conn.commit()
    conn.close()
    flash('점검표가 삭제되었습니다.', 'info')
    return redirect(url_for('admin_equipment'))


@app.route('/admin/equipment/delete/<int:eq_id>')
@admin_required
def admin_equipment_delete(eq_id):
    conn = get_db()
    conn.execute('DELETE FROM equipment WHERE id=?', (eq_id,))
    conn.commit()
    conn.close()
    flash('설비가 삭제되었습니다.', 'info')
    return redirect(url_for('admin_equipment'))


@app.route('/admin/equipment/qr/<int:eq_id>')
@admin_required
def equipment_qr_download(eq_id):
    if not HAS_QRCODE:
        flash('QR 생성 패키지 필요: pip install "qrcode[pil]"', 'error')
        return redirect(url_for('admin_equipment'))
    conn = get_db()
    eq = conn.execute('SELECT * FROM equipment WHERE id=?', (eq_id,)).fetchone()
    conn.close()
    if not eq:
        return '설비를 찾을 수 없습니다.', 404
    url = request.host_url.rstrip('/') + url_for('qr_redirect', code=eq['qr_code'])
    qr  = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True,
                     download_name=f'{eq["name"]}_QR코드.png')


# ── QR 리다이렉트 ─────────────────────────────────────────────────────────────
@app.route('/qr/<code>')
def qr_redirect(code):
    conn = get_db()
    eq = conn.execute('SELECT * FROM equipment WHERE qr_code=?', (code,)).fetchone()
    conn.close()
    if not eq:
        flash('등록되지 않은 QR 코드입니다.', 'error')
        return redirect(url_for('login'))
    if not session.get('user_id'):
        return redirect(url_for('login', next=url_for('inspect', eq_id=eq['id'])))
    return redirect(url_for('inspect', eq_id=eq['id']))


# ── 설비 점검 페이지 ──────────────────────────────────────────────────────────
@app.route('/inspect/<int:eq_id>', methods=['GET', 'POST'])
@login_required
def inspect(eq_id):
    conn = get_db()
    eq = conn.execute('''
        SELECT e.*, a.name AS approver_name
        FROM equipment e
        LEFT JOIN users a ON e.approver_id = a.id
        WHERE e.id=?
    ''', (eq_id,)).fetchone()

    if not eq:
        conn.close()
        flash('설비를 찾을 수 없습니다.', 'error')
        return redirect(url_for('dashboard'))

    is_approver = (
        session.get('is_admin') or
        (session.get('role') == '승인자' and eq['approver_id'] == session['user_id'])
    )
    is_inspector = session.get('role') == '점검자' or session.get('is_admin')

    tmpl = conn.execute(
        'SELECT * FROM inspection_templates WHERE equipment_id=?', (eq_id,)
    ).fetchone()
    tmpl_rows     = json.loads(tmpl['rows']) if tmpl else None
    tmpl_max_cols = tmpl['max_cols']         if tmpl else 0

    db_items = conn.execute(
        'SELECT * FROM inspection_items WHERE equipment_id=? ORDER BY item_order',
        (eq_id,)
    ).fetchall()

    # 오늘 이미 점검됐는지 확인
    today_insp = conn.execute(f'''
        SELECT i.*, u.name AS inspector_name
        FROM inspections i
        JOIN users u ON i.inspector_id = u.id
        WHERE i.equipment_id=? AND {date_col("i.inspected_at")}={TODAY}
        AND i.status IN ('점검완료','승인완료')
        ORDER BY i.inspected_at DESC LIMIT 1
    ''', (eq_id,)).fetchone()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'submit' and is_inspector:
            # 중복 점검 방지
            if today_insp:
                flash('오늘 이미 점검이 진행되었습니다. 중복 점검은 불가합니다.', 'warning')
                return redirect(url_for('inspect', eq_id=eq_id))

            overall_notes = request.form.get('notes', '').strip()

            if db_items:
                item_results = []
                for item in db_items:
                    r_val = request.form.get(f'result_item_{item["id"]}', '정상')
                    n_val = request.form.get(f'notes_item_{item["id"]}', '')
                    item_results.append((item['id'], r_val, n_val))
                all_vals = [r for _, r, _ in item_results]
                overall  = '이상' if '이상' in all_vals else '정상'
                ins_id = conn.insert(
                    "INSERT INTO inspections (equipment_id, inspector_id, result, notes, status) VALUES (?,?,?,?,'점검완료')",
                    (eq_id, session['user_id'], overall, overall_notes)
                )
                for item_id, r_val, n_val in item_results:
                    conn.execute(
                        'INSERT INTO inspection_details (inspection_id, row_index, item_id, result, detail_notes) VALUES (?,?,?,?,?)',
                        (ins_id, 0, item_id, r_val, n_val)
                    )
                conn.commit()
                result = overall

            elif tmpl_rows:
                item_results = []
                for idx, row in enumerate(tmpl_rows):
                    if not row['is_item']:
                        continue
                    r_val = request.form.get(f'result_{idx}', '정상')
                    n_val = request.form.get(f'notes_{idx}', '')
                    item_results.append((idx, r_val, n_val))

                all_vals = [r for _, r, _ in item_results]
                overall  = '이상' if '이상' in all_vals else '정상'

                ins_id = conn.insert(
                    '''INSERT INTO inspections
                       (equipment_id, inspector_id, result, notes, status)
                       VALUES (?,?,?,?,'점검완료')''',
                    (eq_id, session['user_id'], overall, overall_notes)
                )
                for idx, r_val, n_val in item_results:
                    conn.execute(
                        '''INSERT INTO inspection_details
                           (inspection_id, row_index, result, detail_notes)
                           VALUES (?,?,?,?)''',
                        (ins_id, idx, r_val, n_val)
                    )
                conn.commit()
                result = overall
            else:
                result = request.form.get('result', '정상')
                conn.execute(
                    '''INSERT INTO inspections
                       (equipment_id, inspector_id, result, notes, status)
                       VALUES (?,?,?,?,'점검완료')''',
                    (eq_id, session['user_id'], result, overall_notes)
                )
                conn.commit()

            if eq['approver_id']:
                approver = conn.execute(
                    'SELECT name, email FROM users WHERE id=?', (eq['approver_id'],)
                ).fetchone()
                if approver and approver['email']:
                    send_approval_request(
                        to_email       = approver['email'],
                        approver_name  = approver['name'],
                        inspector_name = session['user_name'],
                        eq_name        = eq['name'],
                        location       = eq['location'] or '-',
                        result         = result,
                        notes          = overall_notes,
                        eq_id          = eq_id,
                        host_url       = request.host_url,
                    )

            flash('점검이 완료되었습니다. 승인자에게 알림이 발송됩니다.', 'success')
            return redirect(url_for('inspect', eq_id=eq_id))

        elif action == 'approve' and is_approver:
            ins_id = request.form.get('inspection_id')
            conn.execute(
                f'''UPDATE inspections
                   SET status='승인완료', approved_by=?, approved_at={NOW_FN}
                   WHERE id=? AND status='점검완료' ''',
                (session['user_id'], ins_id)
            )
            conn.commit()
            flash('승인이 완료되었습니다.', 'success')
            return redirect(url_for('inspect', eq_id=eq_id))

    pending_approvals = []
    if is_approver:
        pending_approvals = conn.execute('''
            SELECT i.*, u.name AS inspector_name
            FROM inspections i
            JOIN users u ON i.inspector_id = u.id
            WHERE i.equipment_id=? AND i.status='점검완료'
            ORDER BY i.inspected_at DESC
        ''', (eq_id,)).fetchall()

    history = conn.execute('''
        SELECT i.*, u.name AS inspector_name, a.name AS approved_name
        FROM inspections i
        JOIN users u ON i.inspector_id = u.id
        LEFT JOIN users a ON i.approved_by = a.id
        WHERE i.equipment_id=?
        ORDER BY i.inspected_at DESC
        LIMIT 20
    ''', (eq_id,)).fetchall()
    conn.close()

    now = datetime.now()
    return render_template('inspect.html', eq=eq, history=history,
                           pending_approvals=pending_approvals,
                           is_approver=is_approver, is_inspector=is_inspector,
                           tmpl_rows=tmpl_rows, tmpl_max_cols=tmpl_max_cols,
                           db_items=db_items, today_insp=today_insp,
                           now_year=now.year, now_month=now.month)


# ── 내 점검 결과 ──────────────────────────────────────────────────────────────
@app.route('/my-inspections')
@login_required
def my_inspections():
    date_from     = request.args.get('date_from', '')
    date_to       = request.args.get('date_to', '')
    result_filter = request.args.get('result', '')

    query = '''
        SELECT i.*,
               e.name       AS eq_name,
               e.location   AS eq_location,
               e.department AS eq_dept,
               a.name       AS approved_name
        FROM inspections i
        JOIN equipment e ON i.equipment_id = e.id
        LEFT JOIN users a ON i.approved_by = a.id
        WHERE i.inspector_id = ?
    '''
    params = [session['user_id']]

    if date_from:
        query += f' AND {date_col("i.inspected_at")} >= ?'
        params.append(date_from)
    if date_to:
        query += f' AND {date_col("i.inspected_at")} <= ?'
        params.append(date_to)
    if result_filter:
        query += ' AND i.result = ?'
        params.append(result_filter)

    query += ' ORDER BY i.inspected_at DESC'

    conn = get_db()
    records = conn.execute(query, params).fetchall()

    stats = conn.execute('''
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN result='정상'    THEN 1 ELSE 0 END) AS normal,
            SUM(CASE WHEN result='이상'    THEN 1 ELSE 0 END) AS abnormal,
            SUM(CASE WHEN result='수리필요' THEN 1 ELSE 0 END) AS repair,
            SUM(CASE WHEN result='휴동'    THEN 1 ELSE 0 END) AS idle,
            SUM(CASE WHEN status='승인완료' THEN 1 ELSE 0 END) AS approved
        FROM inspections WHERE inspector_id = ?
    ''', (session['user_id'],)).fetchone()
    conn.close()

    return render_template('my_inspections.html', records=records, stats=stats,
                           date_from=date_from, date_to=date_to,
                           result_filter=result_filter)


# ── 대시보드 ──────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()

    today_count = conn.execute(f'''
        SELECT COUNT(*) AS cnt FROM inspections
        WHERE inspector_id=? AND {date_col("inspected_at")}={TODAY}
    ''', (session['user_id'],)).fetchone()['cnt']

    total_eq = conn.execute(
        'SELECT COUNT(*) AS cnt FROM equipment'
    ).fetchone()['cnt']

    pending_list = []
    if session.get('role') == '승인자' or session.get('is_admin'):
        pending_list = conn.execute('''
            SELECT i.id, i.result, i.inspected_at,
                   e.id AS eq_id, e.name AS eq_name,
                   u.name AS inspector_name
            FROM inspections i
            JOIN equipment e ON i.equipment_id = e.id
            JOIN users u ON i.inspector_id = u.id
            WHERE e.approver_id=? AND i.status='점검완료'
            ORDER BY i.inspected_at DESC
        ''', (session['user_id'],)).fetchall()

    conn.close()
    return render_template('dashboard.html', today_count=today_count,
                           total_eq=total_eq, pending_list=pending_list)


# ── 전체 설비 리스트 ──────────────────────────────────────────────────────────
@app.route('/equipment-list')
@login_required
def equipment_list():
    conn = get_db()
    today_cmp = f"({date_col('latest.inspected_at')} = {TODAY})"
    equipments = conn.execute(f'''
        SELECT e.*,
               latest.result        AS last_result,
               latest.status        AS last_status,
               latest.inspected_at  AS last_inspected,
               u.name               AS inspector_name,
               {today_cmp}          AS inspected_today
        FROM equipment e
        LEFT JOIN inspections latest ON latest.id = (
            SELECT id FROM inspections WHERE equipment_id = e.id ORDER BY inspected_at DESC LIMIT 1
        )
        LEFT JOIN users u ON latest.inspector_id = u.id
        ORDER BY e.name
    ''').fetchall()
    conn.close()
    return render_template('equipment_list.html', equipments=equipments)


# ── 월별 점검결과 HTML 페이지 ─────────────────────────────────────────────────
@app.route('/monthly/<int:eq_id>')
@login_required
def monthly_results(eq_id):
    now   = datetime.now()
    year  = int(request.args.get('year',  now.year))
    month = int(request.args.get('month', now.month))
    ym    = f"{year}-{month:02d}"

    conn = get_db()
    eq = conn.execute('SELECT * FROM equipment WHERE id=?', (eq_id,)).fetchone()
    if not eq:
        conn.close()
        flash('설비를 찾을 수 없습니다.', 'error')
        return redirect(url_for('dashboard'))

    db_items = conn.execute(
        'SELECT * FROM inspection_items WHERE equipment_id=? ORDER BY item_order', (eq_id,)
    ).fetchall()

    tmpl = conn.execute('SELECT * FROM inspection_templates WHERE equipment_id=?', (eq_id,)).fetchone()
    tmpl_rows = json.loads(tmpl['rows']) if tmpl and not db_items else []

    if USE_PG:
        ym_expr = "TO_CHAR(inspected_at::timestamp AT TIME ZONE 'Asia/Seoul','YYYY-MM')"
    else:
        ym_expr = "strftime('%Y-%m', inspected_at)"

    inspections = conn.execute(f'''
        SELECT i.*, u.name AS inspector_name,
               {date_col("i.inspected_at")} AS insp_date
        FROM inspections i
        JOIN users u ON i.inspector_id = u.id
        WHERE i.equipment_id = ? AND {ym_expr} = ?
        ORDER BY i.inspected_at
    ''', (eq_id, ym)).fetchall()

    insp_by_day = {}
    for ins in inspections:
        day = int(str(ins['insp_date']).split('-')[2])
        insp_by_day[day] = ins

    details_by_insp = {}
    for ins in inspections:
        rows = conn.execute(
            'SELECT * FROM inspection_details WHERE inspection_id=?', (ins['id'],)
        ).fetchall()
        if db_items:
            details_by_insp[ins['id']] = {d['item_id']: d for d in rows if d['item_id']}
        else:
            details_by_insp[ins['id']] = {d['row_index']: d for d in rows}

    conn.close()
    days_in_month = calendar.monthrange(year, month)[1]

    return render_template('monthly_results.html',
        eq=eq, db_items=db_items, tmpl_rows=tmpl_rows,
        insp_by_day=insp_by_day, details_by_insp=details_by_insp,
        year=year, month=month, days_in_month=days_in_month,
        now_year=now.year, now_month=now.month)


# ── 월별 점검결과 엑셀 내보내기 ───────────────────────────────────────────────
@app.route('/export/monthly/<int:eq_id>')
@login_required
def export_monthly(eq_id):
    if not HAS_OPENPYXL:
        flash('openpyxl 패키지가 필요합니다.', 'error')
        return redirect(url_for('inspect', eq_id=eq_id))

    now   = datetime.now()
    year  = int(request.args.get('year',  now.year))
    month = int(request.args.get('month', now.month))
    ym    = f"{year}-{month:02d}"

    conn = get_db()
    eq   = conn.execute('SELECT * FROM equipment WHERE id=?', (eq_id,)).fetchone()
    if not eq:
        conn.close()
        flash('설비를 찾을 수 없습니다.', 'error')
        return redirect(url_for('dashboard'))

    tmpl      = conn.execute('SELECT * FROM inspection_templates WHERE equipment_id=?', (eq_id,)).fetchone()
    tmpl_rows = json.loads(tmpl['rows']) if tmpl else []

    # 해당 월 점검 목록
    if USE_PG:
        ym_expr = "TO_CHAR(inspected_at::timestamp AT TIME ZONE 'Asia/Seoul','YYYY-MM')"
    else:
        ym_expr = "strftime('%Y-%m', inspected_at)"

    inspections = conn.execute(f'''
        SELECT i.*, u.name AS inspector_name,
               {date_col("i.inspected_at")} AS insp_date
        FROM inspections i
        JOIN users u ON i.inspector_id = u.id
        WHERE i.equipment_id = ? AND {ym_expr} = ?
        ORDER BY i.inspected_at
    ''', (eq_id, ym)).fetchall()

    # day → inspection 매핑 (같은 날 여러 건이면 마지막 기준)
    insp_by_day = {}
    for ins in inspections:
        day = int(str(ins['insp_date']).split('-')[2])
        insp_by_day[day] = ins

    # 항목별 결과 로드
    details_by_insp = {}
    for ins in inspections:
        rows = conn.execute(
            'SELECT * FROM inspection_details WHERE inspection_id=?', (ins['id'],)
        ).fetchall()
        details_by_insp[ins['id']] = {d['row_index']: d for d in rows}

    conn.close()

    # ── Excel 생성 ────────────────────────────────────────────────────────────
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{month:02d}월 점검결과"

    days_in_month = calendar.monthrange(year, month)[1]
    total_cols    = 4 + days_in_month  # 번호+항목+기준+단위 + 일수

    # 스타일 정의
    def fill(hex_):  return PatternFill('solid', fgColor=hex_)
    def font(bold=False, color='111827', size=9):
        return Font(bold=bold, color=color, size=size)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left   = Alignment(horizontal='left',   vertical='center', wrap_text=True)
    thin   = Side(style='thin', color='D1D5DB')
    bdr    = Border(left=thin, right=thin, top=thin, bottom=thin)

    ORANGE_FILL  = fill('F97316')
    LIGHT_FILL   = fill('FFF7ED')
    GREEN_FILL   = fill('DCFCE7')
    RED_FILL     = fill('FEE2E2')
    GRAY_FILL    = fill('F3F4F6')
    EMPTY_FILL   = fill('F9FAFB')
    SECTION_FILL = fill('FFEDD5')

    def hdr_cell(row, col, value):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color='FFFFFF', size=9)
        c.fill = ORANGE_FILL
        c.alignment = center
        c.border = bdr
        return c

    # ── 1행: 제목 ────────────────────────────────────────────────────────────
    ws.merge_cells(f'A1:{get_column_letter(total_cols)}1')
    c = ws['A1']
    c.value     = f"{eq['name']}  |  {year}년 {month}월 점검결과표"
    c.font      = Font(bold=True, color='FFFFFF', size=13)
    c.fill      = ORANGE_FILL
    c.alignment = center
    ws.row_dimensions[1].height = 32

    # ── 2행: 설비 정보 ────────────────────────────────────────────────────────
    info = [('설치위치', eq['location'] or '-'), ('부서', eq['department'] or '-')]
    col = 1
    for label, val in info:
        lc = ws.cell(row=2, column=col, value=label)
        lc.font = Font(bold=True, size=9); lc.fill = LIGHT_FILL; lc.alignment = center; lc.border = bdr
        vc = ws.cell(row=2, column=col+1, value=val)
        vc.font = font(size=9); vc.fill = LIGHT_FILL; vc.alignment = left; vc.border = bdr
        col += 2
    # 나머지 셀 채우기
    for c2 in range(col, total_cols+1):
        ws.cell(row=2, column=c2).fill = LIGHT_FILL
        ws.cell(row=2, column=c2).border = bdr
    ws.row_dimensions[2].height = 18

    # ── 4행: 컬럼 헤더 ────────────────────────────────────────────────────────
    HDR_ROW = 4
    for col, h in enumerate(['번호', '점검항목', '판단기준', '단위'], 1):
        hdr_cell(HDR_ROW, col, h)
    for day in range(1, days_in_month+1):
        hdr_cell(HDR_ROW, 4+day, f"{day}")
    ws.row_dimensions[HDR_ROW].height = 20

    # ── 열 너비 ────────────────────────────────────────────────────────────────
    ws.column_dimensions['A'].width = 5
    ws.column_dimensions['B'].width = 26
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 7
    for day in range(1, days_in_month+1):
        ws.column_dimensions[get_column_letter(4+day)].width = 5

    # ── 데이터 행 ─────────────────────────────────────────────────────────────
    data_row = HDR_ROW + 1
    item_num = 0

    for row_idx, row in enumerate(tmpl_rows):
        cells = row['cells']

        if not row['is_item']:
            # 섹션 헤더
            ws.merge_cells(f'A{data_row}:{get_column_letter(total_cols)}{data_row}')
            c = ws.cell(row=data_row, column=1, value=' '.join(cells))
            c.font = Font(bold=True, size=9, color='EA580C')
            c.fill = SECTION_FILL; c.alignment = left; c.border = bdr
            for col in range(2, total_cols+1):
                ws.cell(row=data_row, column=col).fill = SECTION_FILL
                ws.cell(row=data_row, column=col).border = bdr
            ws.row_dimensions[data_row].height = 18
            data_row += 1
            continue

        item_num += 1
        # 번호
        c = ws.cell(row=data_row, column=1, value=item_num)
        c.font = font(); c.alignment = center; c.border = bdr

        # 점검항목
        item_name = cells[1] if len(cells) > 1 else (cells[0] if cells else '')
        c = ws.cell(row=data_row, column=2, value=item_name)
        c.font = font(); c.alignment = left; c.border = bdr

        # 판단기준
        c = ws.cell(row=data_row, column=3, value=cells[2] if len(cells) > 2 else '')
        c.font = font(); c.alignment = left; c.border = bdr

        # 단위
        c = ws.cell(row=data_row, column=4, value=cells[3] if len(cells) > 3 else '')
        c.font = font(); c.alignment = center; c.border = bdr

        # 일별 결과
        for day in range(1, days_in_month+1):
            c = ws.cell(row=data_row, column=4+day)
            c.alignment = center; c.border = bdr

            if day in insp_by_day:
                ins = insp_by_day[day]
                detail = details_by_insp.get(ins['id'], {}).get(row_idx)
                if detail:
                    res = detail['result']
                    if res == '정상':
                        c.value = 'O'; c.fill = GREEN_FILL
                        c.font = Font(bold=True, size=9, color='15803D')
                    elif res == '이상':
                        c.value = 'X'; c.fill = RED_FILL
                        c.font = Font(bold=True, size=9, color='DC2626')
                    else:
                        c.value = '-'; c.fill = GRAY_FILL
                        c.font = Font(size=9, color='6B7280')
                else:
                    c.fill = EMPTY_FILL
            else:
                c.fill = EMPTY_FILL

        ws.row_dimensions[data_row].height = 18
        data_row += 1

    # ── 점검자 행 ─────────────────────────────────────────────────────────────
    ws.merge_cells(f'A{data_row}:D{data_row}')
    c = ws.cell(row=data_row, column=1, value='점검자')
    c.font = Font(bold=True, size=9); c.fill = LIGHT_FILL
    c.alignment = center; c.border = bdr

    for day in range(1, days_in_month+1):
        c = ws.cell(row=data_row, column=4+day)
        c.border = bdr; c.alignment = center
        c.font = Font(size=7)
        if day in insp_by_day:
            c.value = insp_by_day[day]['inspector_name']
            c.fill = LIGHT_FILL
        else:
            c.fill = EMPTY_FILL
    ws.row_dimensions[data_row].height = 16
    data_row += 1

    # ── 범례 ──────────────────────────────────────────────────────────────────
    ws.merge_cells(f'A{data_row}:{get_column_letter(total_cols)}{data_row}')
    c = ws.cell(row=data_row, column=1,
                value='※ O: 정상   X: 이상   -: 해당없음   빈칸: 미점검')
    c.font = Font(size=8, italic=True, color='6B7280')
    c.alignment = left

    # ── 출력 ──────────────────────────────────────────────────────────────────
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{eq['name']}_{year}년{month:02d}월_점검결과.xlsx"
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


# ── 로그아웃 ──────────────────────────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    debug = not USE_PG
    print("=" * 50)
    print("  INTOPS 설비점검 시스템 시작")
    print(f"  http://localhost:{port} 으로 접속하세요")
    print("  기본 관리자 계정: admin / admin123")
    print("=" * 50)
    app.run(debug=debug, host='0.0.0.0', port=port)
