# Flask 웹앱 개발 블루프린트

> 이 문서는 INTOPS 설비점검 시스템을 개발하면서 정립한 패턴·구조·코드 스니펫 모음입니다.  
> 새 프로젝트를 시작할 때 이 파일을 Claude에게 주면 동일한 방식으로 빠르게 구현할 수 있습니다.

---

## 1. 기술 스택

| 항목 | 선택 |
|---|---|
| 백엔드 | Python · Flask >= 3.0 |
| DB (로컬) | SQLite3 |
| DB (운영) | PostgreSQL (Render 호스팅) |
| 인증 | werkzeug `generate_password_hash` / `check_password_hash` |
| 이메일 | smtplib (Gmail SMTP TLS) |
| 차트 | Chart.js 4.x CDN |
| 엑셀 출력 | openpyxl |
| 이미지 압축 | 브라우저 Canvas API (JS) |
| PWA | manifest.json + service worker |
| 배포 | Render (gunicorn) |

---

## 2. 프로젝트 구조

```
project/
├── app.py                  # 라우트·DB·비즈니스 로직 전체
├── email_config.py         # SMTP 설정 (환경변수 로드)
├── requirements.txt
├── render.yaml             # Render 배포 설정
├── static/
│   ├── style.css           # 전역 CSS (다크테마 포함)
│   ├── logo.png
│   ├── manifest.json       # PWA
│   ├── sw.js               # Service Worker
│   └── nav.js              # 모바일 햄버거 메뉴
└── templates/
    ├── login.html
    ├── register.html
    ├── dashboard.html      # 홈 (통계 카드 + 시스템 안내)
    ├── admin.html          # 회원 관리 (승인/역할/비밀번호초기화/삭제)
    ├── help.html           # 도움말
    └── [기능별].html
```

---

## 3. DB 연결 패턴 — SQLite / PostgreSQL 이중 지원

```python
# app.py 상단부 — 환경에 따라 자동 전환
import os, sqlite3

DATABASE = 'data.db'

class DBConn:
    """sqlite3.Connection 래퍼: _pg=False / psycopg2 래퍼: _pg=True"""
    def __init__(self, conn, pg=False):
        self._conn = conn
        self._pg   = pg
    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur
    def commit(self):   self._conn.commit()
    def close(self):    self._conn.close()

def get_db():
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('postgres'):
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
        return DBConn(conn, pg=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return DBConn(conn, pg=False)
```

### 플레이스홀더 패턴 (필수 습관)
```python
conn = get_db()
ph   = '%s' if conn._pg else '?'

# 쿼리 작성 시 항상 ph 사용
row = conn.execute(
    f'SELECT * FROM users WHERE id={ph}', (user_id,)
).fetchone()
```

### PostgreSQL 전용 주의사항
```python
# TEXT 컬럼을 TIMESTAMP와 비교할 때 반드시 캐스팅
# ❌ 틀림:  WHERE occurred_at >= NOW() - INTERVAL '12 months'
# ✅ 맞음:
if conn._pg:
    sql = "WHERE occurred_at::timestamp >= NOW() - INTERVAL '12 months'"
    group_by = "TO_CHAR(occurred_at::timestamp, 'YYYY-MM')"
else:
    sql = "WHERE occurred_at >= datetime('now','localtime','-12 months')"
    group_by = "strftime('%Y-%m', occurred_at)"
```

---

## 4. DB 스키마 초기화 패턴

```python
def init_db():
    conn = get_db()
    _pk  = 'SERIAL PRIMARY KEY' if conn._pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    ph   = '%s' if conn._pg else '?'

    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id          {_pk},
            name        TEXT NOT NULL,
            employee_id TEXT UNIQUE NOT NULL,
            email       TEXT DEFAULT '',
            phone       TEXT NOT NULL,
            team        TEXT NOT NULL,
            password    TEXT NOT NULL,
            role        TEXT DEFAULT '점검자',
            is_admin    INTEGER DEFAULT 0,
            is_approved INTEGER DEFAULT 0,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

    # 관리자 계정 초기 생성
    admin_pw = generate_password_hash(os.environ.get('ADMIN_PASSWORD', 'admin1234'))
    if conn._pg:
        conn.execute(f'''
            INSERT INTO users (name, employee_id, email, phone, team, password, role, is_admin, is_approved)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},1,1)
            ON CONFLICT (employee_id) DO NOTHING
        ''', ('관리자','admin','admin@company.com','010-0000-0000','관리팀',admin_pw,'승인자'))
    else:
        conn.execute(f'''
            INSERT OR IGNORE INTO users
                (name, employee_id, email, phone, team, password, role, is_admin, is_approved)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},1,1)
        ''', ('관리자','admin','admin@company.com','010-0000-0000','관리팀',admin_pw,'승인자'))
    conn.commit()
    conn.close()
```

---

## 5. 인증 시스템

### 데코레이터
```python
from functools import wraps
from flask import session, redirect, url_for

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return wrapper
```

### 로그인 라우트 (사번 OR 전화번호)
```python
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        import re as _re
        identifier  = request.form['employee_id'].strip()
        raw_pw      = request.form['password']
        digits_only = _re.sub(r'\D', '', identifier)  # 전화번호 숫자 추출

        conn = get_db()
        ph   = '%s' if conn._pg else '?'
        user = conn.execute(
            f"SELECT * FROM users WHERE employee_id={ph} "
            f"OR REPLACE(REPLACE(phone,'-',' '),' ','')={ph} "
            f"OR REPLACE(phone,'-','')={ph} LIMIT 1",
            (identifier, digits_only, digits_only)
        ).fetchone()

        if user and check_password_hash(user['password'], raw_pw):
            if not user['is_approved']:
                flash('관리자 승인 대기 중입니다.', 'warning')
                return render_template('login.html')
            session.permanent = True
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['is_admin']  = bool(user['is_admin'])
            session['role']      = user['role'] or '점검자'
            session['team']      = user.get('team') or ''
            return redirect(url_for('dashboard'))
        else:
            flash('사번(또는 전화번호) 또는 비밀번호가 올바르지 않습니다.', 'error')
    return render_template('login.html')
```

### 회원가입 (사번 선택 입력)
```python
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        import re as _re
        name   = request.form['name'].strip()
        emp_id = request.form.get('employee_id', '').strip()
        phone  = request.form['phone'].strip()
        # 사번 미입력 시 전화번호를 내부 식별자로 사용 (UNIQUE 충족)
        if not emp_id:
            emp_id = _re.sub(r'\D', '', phone) or phone
        ...
```

---

## 6. 이메일 발송 패턴

### email_config.py
```python
import os

ENABLED  = bool(os.environ.get('SMTP_USER'))
HOST     = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
PORT     = int(os.environ.get('SMTP_PORT', 587))
USER     = os.environ.get('SMTP_USER', '')
PASSWORD = os.environ.get('SMTP_PASSWORD', '')
FROM     = os.environ.get('SMTP_FROM', USER)
```

### 공통 발송 함수
```python
import smtplib, threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import email_config

def _send_mail(to_addr: str, subject: str, html_body: str):
    """동기 발송 — 항상 threading.Thread로 감싸서 호출할 것"""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = email_config.FROM
    msg['To']      = to_addr
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP(email_config.HOST, email_config.PORT) as s:
        s.starttls()
        s.login(email_config.USER, email_config.PASSWORD)
        s.sendmail(email_config.FROM, to_addr, msg.as_string())

def send_notification(to_addr: str, subject: str, html_body: str):
    """비동기 발송 — 라우트에서 이 함수를 호출"""
    if not email_config.ENABLED:
        return
    threading.Thread(
        target=_send_mail,
        args=(to_addr, subject, html_body),
        daemon=True
    ).start()
```

### 이름으로 이메일 조회 후 발송 (담당자 자동 알림)
```python
def send_notification_by_name(person_name: str, subject: str, html_body: str):
    """users 테이블에서 이름으로 이메일 조회 후 발송"""
    def _lookup_and_send():
        conn2 = get_db()
        ph2   = '%s' if conn2._pg else '?'
        row   = conn2.execute(
            f"SELECT email FROM users WHERE name={ph2} LIMIT 1",
            (person_name.strip(),)
        ).fetchone()
        conn2.close()
        if row and row['email']:
            _send_mail(row['email'], subject, html_body)
    threading.Thread(target=_lookup_and_send, daemon=True).start()
```

---

## 7. 공통 HTML 구조

### topbar (모든 페이지 공통)
```html
<div class="topbar">
  <div class="topbar-title">
    <img src="/static/logo.png" style="width:28px;height:28px;border-radius:6px;object-fit:cover;vertical-align:middle;margin-right:8px;">
    시스템명
  </div>
  <div class="topbar-nav">
    <a href="/dashboard" class="nav-link">홈</a>
    <a href="/feature1"  class="nav-link">기능1</a>
    <a href="/feature2"  class="nav-link active">기능2</a>
    {% if session.is_admin %}
    <a href="/admin" class="nav-link">관리자</a>
    {% endif %}
  </div>
  <div class="topbar-user">
    <span class="role-badge-sm">{{ session.role }}</span>
    {{ session.user_name }} 님
    <a href="/help" class="help-btn">❓ 도움말</a>
    <a href="/my-profile" class="help-btn" style="background:rgba(249,115,22,0.1);color:var(--orange);border:1px solid rgba(249,115,22,0.25);">👤 내 정보</a>
    <a href="/logout">로그아웃</a>
  </div>
</div>
```

### 페이지 래퍼 (표준 / 넓은 버전)
```html
<!-- 표준 (max-width: 960px) -->
<div class="page-wrap" style="align-items:flex-start; padding-top:32px;">
  <div class="card card-wide">
    ...
  </div>
</div>

<!-- 넓은 버전 (테이블 중심 페이지: 1500px+) -->
<div class="page-wrap" style="align-items:flex-start; padding-top:28px;">
  <div class="card card-wide" style="max-width:1560px; width:100%;">
    ...
  </div>
</div>
```

### 플래시 메시지 (모든 페이지)
```html
{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}
    <div class="alert alert-{{ category }}">{{ message }}</div>
  {% endfor %}
{% endwith %}
```

### 모달 패턴 (범용)
```html
<!-- 트리거 버튼 -->
<button onclick="openModal()">열기</button>

<!-- 모달 -->
<div id="myModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55);
     z-index:9999; align-items:center; justify-content:center;">
  <div style="background:var(--card-bg); border:1px solid var(--border); border-radius:14px;
              padding:32px 28px; min-width:340px; max-width:480px; width:90%;
              box-shadow:0 8px 40px rgba(0,0,0,0.4);">
    <h3 style="margin:0 0 16px; font-size:1.1rem;">제목</h3>
    <!-- 내용 -->
    <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:20px;">
      <button onclick="closeModal()" class="btn btn-outline">취소</button>
      <button onclick="confirm()">확인</button>
    </div>
  </div>
</div>

<script>
function openModal()  { document.getElementById('myModal').style.display = 'flex'; }
function closeModal() { document.getElementById('myModal').style.display = 'none'; }
document.getElementById('myModal').addEventListener('click', e => {
  if (e.target === document.getElementById('myModal')) closeModal();
});
</script>
```

---

## 8. 테이블 + 인라인 폼 패턴

> 테이블 행을 한 줄로 유지하고, 수정 폼은 토글로 펼치는 패턴

```html
<style>
  .data-table { width:100%; border-collapse:collapse; font-size:0.84rem; }
  .data-table th {
    background:rgba(249,115,22,0.08); color:var(--orange-dark);
    font-weight:700; padding:10px 12px; text-align:left;
    border-bottom:2px solid rgba(249,115,22,0.2); white-space:nowrap;
  }
  .data-table td { padding:8px 10px; border-bottom:1px solid var(--border-light); vertical-align:middle; }
  .row-summary  { display:flex; flex-direction:column; gap:2px; }
  .row-form-wrap { display:none; }
  .cell-text    { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:180px; }
</style>

<!-- 행 -->
<tr>
  <td style="max-width:200px;">
    <!-- 기본: 요약 텍스트 -->
    <div class="row-summary" id="view-{{ item.id }}">
      <div class="cell-text" title="{{ item.content }}">{{ item.content or '—' }}</div>
      <button class="btn-sm" onclick="toggleRowForm({{ item.id }})">✏️ 수정</button>
    </div>
    <!-- 펼침: 편집 폼 -->
    <div class="row-form-wrap" id="form-{{ item.id }}">
      <form method="POST" action="/item/{{ item.id }}/update">
        <textarea name="content">{{ item.content }}</textarea>
        <div style="display:flex;gap:5px;margin-top:4px;">
          <button type="submit" class="btn-sm">💾 저장</button>
          <button type="button" class="btn-sm btn-sm-red" onclick="toggleRowForm({{ item.id }})">✕ 취소</button>
        </div>
      </form>
    </div>
  </td>
</tr>

<script>
function toggleRowForm(id) {
  const view = document.getElementById('view-' + id);
  const form = document.getElementById('form-' + id);
  const show = form.style.display === 'none' || !form.style.display;
  view.style.display = show ? 'none' : 'flex';
  form.style.display = show ? 'block' : 'none';
}
</script>
```

---

## 9. 이미지 업로드 / 압축 패턴 (JS)

```javascript
const MAX_PX  = 1280;
const QUALITY = 0.72;
const MAX_PHOTOS = 5;

function compressImage(file) {
  return new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = e => {
      const img = new Image();
      img.onload = () => {
        let w = img.width, h = img.height;
        if (w > MAX_PX || h > MAX_PX) {
          if (w >= h) { h = Math.round(h * MAX_PX / w); w = MAX_PX; }
          else        { w = Math.round(w * MAX_PX / h); h = MAX_PX; }
        }
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        resolve({ data: canvas.toDataURL('image/jpeg', QUALITY), name: file.name });
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}

async function handlePhotoSelect(input, previewId, formId) {
  const preview  = document.getElementById(previewId);
  const form     = document.getElementById(formId);
  const existing = form.querySelectorAll('input[name^="photo_data_"]').length;
  const files    = Array.from(input.files).slice(0, MAX_PHOTOS - existing);

  for (let i = 0; i < files.length; i++) {
    const idx        = existing + i + 1;
    const compressed = await compressImage(files[i]);

    // 히든 입력으로 base64 전송
    const hd = Object.assign(document.createElement('input'),
      { type:'hidden', name:`photo_data_${idx}`, value: compressed.data });
    const hn = Object.assign(document.createElement('input'),
      { type:'hidden', name:`photo_name_${idx}`, value: compressed.name });
    form.appendChild(hd); form.appendChild(hn);

    // 미리보기
    const item = document.createElement('div');
    item.style.cssText = 'position:relative;width:80px;height:80px;';
    const imgEl = document.createElement('img');
    imgEl.src = compressed.data;
    imgEl.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:8px;';
    const del = document.createElement('button');
    del.type = 'button'; del.textContent = '✕';
    del.style.cssText = 'position:absolute;top:-6px;right:-6px;width:20px;height:20px;background:#dc2626;color:#fff;border:none;border-radius:50%;font-size:0.7rem;cursor:pointer;';
    del.onclick = () => { hd.remove(); hn.remove(); item.remove(); };
    item.appendChild(imgEl); item.appendChild(del);
    preview.appendChild(item);
  }
  input.value = '';
}
```

### 서버: base64 이미지 저장
```python
import base64, os, uuid

UPLOAD_DIR = 'static/uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_photo_from_base64(b64_data: str, filename: str) -> str:
    """base64 → 파일 저장 후 경로 반환"""
    header, data = b64_data.split(',', 1)
    ext  = 'jpg'
    name = f'{uuid.uuid4().hex}.{ext}'
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(data))
    return path

# 라우트에서 사용
for i in range(1, 6):
    b64 = request.form.get(f'photo_data_{i}')
    if b64:
        path = save_photo_from_base64(b64, request.form.get(f'photo_name_{i}', ''))
        conn.execute('INSERT INTO photos (record_id, path) VALUES (?,?)', (record_id, path))
```

---

## 10. 필터 + 검색 패턴

```python
# 라우트에서 동적 WHERE 생성
def build_filter_query(base_sql, conn):
    ph = '%s' if conn._pg else '?'
    f_team     = request.args.get('team', '').strip()
    f_status   = request.args.get('status', '').strip()
    f_priority = request.args.get('priority', '').strip()
    f_keyword  = request.args.get('q', '').strip()

    where_parts, params = [], []

    if f_team:
        where_parts.append(f'department = {ph}'); params.append(f_team)
    if f_status in ('0', '1'):
        where_parts.append(f'is_done = {ph}'); params.append(int(f_status))
    if f_priority:
        where_parts.append(f'priority = {ph}'); params.append(f_priority)
    if f_keyword:
        where_parts.append(f'(title LIKE {ph} OR content LIKE {ph})')
        params.extend([f'%{f_keyword}%', f'%{f_keyword}%'])

    if where_parts:
        base_sql += ' WHERE ' + ' AND '.join(where_parts)

    return base_sql, params
```

```html
<!-- 필터 바 HTML -->
<form method="GET" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:16px;">
  <select name="team" onchange="this.form.submit()">
    <option value="">전체 팀</option>
    {% for t in teams %}
    <option value="{{ t }}" {% if f_team==t %}selected{% endif %}>{{ t }}</option>
    {% endfor %}
  </select>
  <select name="status" onchange="this.form.submit()">
    <option value="">전체 상태</option>
    <option value="0" {% if f_status=='0' %}selected{% endif %}>미완료</option>
    <option value="1" {% if f_status=='1' %}selected{% endif %}>완료</option>
  </select>
  <input type="text" name="q" value="{{ f_keyword }}" placeholder="검색어...">
  <button type="submit">🔍 검색</button>
  {% if f_team or f_status or f_keyword %}
  <a href="{{ request.path }}" style="color:#dc2626;">✕ 초기화</a>
  {% endif %}
</form>
```

---

## 11. 엑셀 다운로드 패턴

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from flask import Response
import io

@app.route('/export')
@login_required
def export_excel():
    conn = get_db()
    rows = conn.execute('SELECT * FROM records ORDER BY created_at DESC').fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = '데이터'

    headers = ['번호', '제목', '상태', '담당자', '등록일']
    header_fill = PatternFill('solid', fgColor='F97316')
    header_font = Font(bold=True, color='FFFFFF')

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill   = header_fill
        cell.font   = header_font
        cell.alignment = Alignment(horizontal='center')

    for r_idx, row in enumerate(rows, 2):
        ws.cell(r_idx, 1, r_idx - 1)
        ws.cell(r_idx, 2, row['title'])
        ws.cell(r_idx, 3, '완료' if row['is_done'] else '미완료')
        ws.cell(r_idx, 4, row['assignee'])
        ws.cell(r_idx, 5, str(row['created_at'])[:10])

    # 열 너비 자동 조정
    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=export.xlsx'}
    )
```

---

## 12. Chart.js 공통 설정

```javascript
// 다크모드 대응 공통 색상
const isDark   = document.documentElement.classList.contains('dark');
const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
const tickColor = isDark ? '#9ca3af' : '#6b7280';

Chart.defaults.font.family = "'Malgun Gothic','맑은 고딕',sans-serif";
Chart.defaults.font.size   = 11;

// 막대 차트 예시
new Chart(document.getElementById('myChart'), {
  type: 'bar',
  data: {
    labels: {{ labels | tojson }},
    datasets: [{
      label: '건수',
      data: {{ values | tojson }},
      backgroundColor: 'rgba(249,115,22,0.8)',
      borderRadius: 4,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: tickColor } } },
    scales: {
      x: { grid: { color: gridColor }, ticks: { color: tickColor } },
      y: { beginAtZero: true, grid: { color: gridColor }, ticks: { color: tickColor, stepSize: 1 } }
    }
  }
});
```

---

## 13. 관리자 페이지 공통 기능

| 기능 | 라우트 | 비고 |
|---|---|---|
| 회원 목록 | `GET /admin` | 승인대기 / 승인완료 분리 |
| 회원 승인 | `POST /admin/approve/<id>` | 역할 함께 지정 |
| 회원 거부 | `GET /admin/reject/<id>` | |
| 역할 변경 | `POST /admin/change-role/<id>` | 점검자 / 승인자 |
| 비밀번호 초기화 | `POST /admin/reset-password/<id>` | 임시PW 생성 또는 직접지정 |
| 회원 삭제 | `GET /admin/delete-user/<id>` | 본인 삭제 불가 |

### 비밀번호 초기화 라우트
```python
@app.route('/admin/reset-password/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    import secrets, string
    conn = get_db()
    ph   = '%s' if conn._pg else '?'
    user = conn.execute(f'SELECT name FROM users WHERE id={ph}', (user_id,)).fetchone()
    if not user:
        flash('사용자를 찾을 수 없습니다.', 'error')
        return redirect(url_for('admin'))
    new_pw = request.form.get('new_password', '').strip()
    if not new_pw:
        new_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
    if len(new_pw) < 6:
        flash('비밀번호는 6자 이상이어야 합니다.', 'error')
        return redirect(url_for('admin'))
    conn.execute(f'UPDATE users SET password={ph} WHERE id={ph}',
                 (generate_password_hash(new_pw), user_id))
    conn.commit()
    conn.close()
    flash(f'✅ [{user["name"]}] 임시 비밀번호: {new_pw}', 'success')
    return redirect(url_for('admin'))
```

---

## 14. IP 로그인 실패 잠금

```python
from collections import defaultdict
from datetime import datetime, timedelta

_login_fails = defaultdict(list)   # {ip: [datetime, ...]}
MAX_FAIL     = 5
LOCK_MINUTES = 10

def _check_login_lock(ip: str) -> int:
    """잠금 중이면 남은 분, 아니면 0"""
    now   = datetime.now()
    fails = [t for t in _login_fails[ip] if now - t < timedelta(minutes=LOCK_MINUTES)]
    _login_fails[ip] = fails
    if len(fails) >= MAX_FAIL:
        remain = LOCK_MINUTES - int((now - fails[0]).total_seconds() / 60)
        return max(remain, 1)
    return 0

def _record_login_fail(ip: str):
    _login_fails[ip].append(datetime.now())

def _reset_login_fail(ip: str):
    _login_fails.pop(ip, None)
```

---

## 15. PWA 설정

### static/manifest.json
```json
{
  "name": "시스템명",
  "short_name": "시스템명",
  "start_url": "/dashboard",
  "display": "standalone",
  "background_color": "#0f1623",
  "theme_color": "#f97316",
  "icons": [
    { "src": "/static/logo.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/logo.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

### static/sw.js (오프라인 캐시 기본)
```javascript
const CACHE = 'v1';
const ASSETS = ['/static/style.css', '/static/logo.png'];
self.addEventListener('install',  e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(ks =>
  Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))));
self.addEventListener('fetch', e => e.respondWith(
  caches.match(e.request).then(r => r || fetch(e.request))
));
```

### 모든 HTML head에 추가
```html
<link rel="manifest" href="/static/manifest.json">
<link rel="icon" href="/static/logo.png" type="image/png">
<script>if('serviceWorker' in navigator){ navigator.serviceWorker.register('/static/sw.js'); }</script>
```

---

## 16. Render 배포 설정

### render.yaml
```yaml
services:
  - type: web
    name: my-app
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: my-db
          property: connectionString
      - key: SECRET_KEY
        generateValue: true
      - key: ADMIN_PASSWORD
        sync: false
      - key: SMTP_USER
        sync: false
      - key: SMTP_PASSWORD
        sync: false

databases:
  - name: my-db
    plan: free
```

### requirements.txt
```
flask>=3.0.0
werkzeug>=3.0.0
psycopg2-binary
gunicorn
openpyxl
```

---

## 17. 새 프로젝트 시작 시 Claude 프롬프트 예시

```
이 BLUEPRINT.md를 참고해서 [프로그램명] 웹앱을 만들어줘.

기능 목록:
- 로그인/회원가입 (사번+전화번호 로그인, 관리자 승인)
- [기능A]: ...
- [기능B]: ...
- 관리자 페이지: 회원 관리, 비밀번호 초기화
- 엑셀 다운로드, 프린트

DB 테이블:
- users (기존 패턴 동일)
- [테이블명]: 컬럼1, 컬럼2, ...

팀 목록: ['팀A', '팀B', '팀C']

SQLite(로컬) + PostgreSQL(Render) 이중 지원, 다크테마 CSS 동일하게 적용.
```

---

## 18. 기능 구현 체크리스트

새 프로젝트 시작 시 아래 항목을 순서대로 구현합니다.

- [ ] `app.py` — Flask 앱 초기화, SECRET_KEY, 세션 설정
- [ ] `get_db()` — DBConn 래퍼, SQLite/PostgreSQL 자동 전환
- [ ] `init_db()` — 테이블 생성, 관리자 계정 초기화
- [ ] `email_config.py` — SMTP 환경변수 로드
- [ ] 인증 데코레이터 — `login_required`, `admin_required`
- [ ] 로그인/로그아웃/회원가입 라우트
- [ ] `dashboard.html` — 통계 카드 + 시스템 안내
- [ ] 핵심 기능 라우트 + 템플릿
- [ ] 관리자 페이지 — 승인/역할/비밀번호초기화/삭제
- [ ] 엑셀 다운로드
- [ ] 이메일 알림 (필요 시)
- [ ] `static/style.css` — 다크테마 CSS 복사
- [ ] `static/manifest.json` + `sw.js` — PWA
- [ ] `render.yaml` + `requirements.txt` — 배포 설정
- [ ] `help.html` — 기능별 도움말
