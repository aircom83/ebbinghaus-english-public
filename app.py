"""
エビングハウスの忘却曲線 英語学習ツール（マルチユーザー版）
Flask + PostgreSQL / Render デプロイ対応
"""

import json
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# ===== アプリ設定 =====

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

database_url = os.environ.get("DATABASE_URL", "sqlite:///ebbinghaus.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db = SQLAlchemy(app)

REVIEW_INTERVALS = [1, 3, 7, 14, 30]


# ===== モデル =====

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    entries = db.relationship("Entry", backref="user", lazy=True, cascade="all, delete-orphan")


class Entry(db.Model):
    __tablename__ = "entries"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    japanese = db.Column(db.String(500), nullable=False)
    english = db.Column(db.String(500), nullable=False)
    registered_at = db.Column(db.String(10), nullable=False)
    schedule = db.Column(db.Text, nullable=False)  # JSON array
    next_review_index = db.Column(db.Integer, default=0)
    history = db.Column(db.Text, default="[]")  # JSON array
    completed = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "japanese": self.japanese,
            "english": self.english,
            "registered_at": self.registered_at,
            "schedule": json.loads(self.schedule),
            "next_review_index": self.next_review_index,
            "history": json.loads(self.history),
            "completed": self.completed,
        }


# ===== ヘルパー =====

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "ログインしてください"}), 401
        return f(*args, **kwargs)
    return decorated


def generate_schedule(registered_date_str):
    reg_date = datetime.strptime(registered_date_str, "%Y-%m-%d").date()
    return [(reg_date + timedelta(days=d)).isoformat() for d in REVIEW_INTERVALS]


def get_today_str():
    return datetime.now().date().isoformat()


# ===== ルート =====

@app.route("/")
def index():
    return HTML_PAGE


# --- 認証 API ---

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "ユーザー名とパスワードを入力してください"}), 400
    if len(username) < 2:
        return jsonify({"error": "ユーザー名は2文字以上にしてください"}), 400
    if len(password) < 4:
        return jsonify({"error": "パスワードは4文字以上にしてください"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "このユーザー名は既に使われています"}), 400
    user = User(username=username, password_hash=generate_password_hash(password, method='pbkdf2:sha256'))
    db.session.add(user)
    db.session.commit()
    session["user_id"] = user.id
    session["username"] = user.username
    return jsonify({"ok": True, "username": user.username})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "ユーザー名とパスワードを入力してください"}), 400
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "ユーザー名またはパスワードが間違っています"}), 401
    session["user_id"] = user.id
    session["username"] = user.username
    return jsonify({"ok": True, "username": user.username})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "user_id" in session:
        return jsonify({"logged_in": True, "username": session.get("username")})
    return jsonify({"logged_in": False})


# --- データ API ---

@app.route("/api/data")
@login_required
def api_data():
    uid = session["user_id"]
    today = get_today_str()
    entries = Entry.query.filter_by(user_id=uid).all()
    entries_list = [e.to_dict() for e in entries]
    reviews = []
    for e in entries:
        if e.completed:
            continue
        sched = json.loads(e.schedule)
        idx = e.next_review_index
        if idx < len(sched) and sched[idx] <= today:
            reviews.append(e.id)
    total = len(entries_list)
    completed = sum(1 for e in entries_list if e["completed"])
    return jsonify({
        "entries": entries_list,
        "today": today,
        "review_count": len(reviews),
        "review_ids": reviews,
        "total": total,
        "completed": completed,
        "active": total - completed,
    })


@app.route("/api/reviews")
@login_required
def api_reviews():
    uid = session["user_id"]
    today = get_today_str()
    entries = Entry.query.filter_by(user_id=uid, completed=False).all()
    reviews = []
    for e in entries:
        sched = json.loads(e.schedule)
        idx = e.next_review_index
        if idx < len(sched) and sched[idx] <= today:
            reviews.append(e.to_dict())
    return jsonify({"reviews": reviews})


@app.route("/api/today-practiced")
@login_required
def api_today_practiced():
    uid = session["user_id"]
    today = get_today_str()
    entries = Entry.query.filter_by(user_id=uid).all()
    practiced = []
    for e in entries:
        hist = json.loads(e.history)
        if any(h["date"] == today for h in hist):
            practiced.append(e.to_dict())
    return jsonify({"entries": practiced})


@app.route("/api/add", methods=["POST"])
@login_required
def api_add():
    uid = session["user_id"]
    data = request.get_json()
    jp = data.get("japanese", "").strip()
    en = data.get("english", "").strip()
    if not jp or not en:
        return jsonify({"error": "両方入力してください"}), 400
    today = get_today_str()
    entry = Entry(
        user_id=uid, japanese=jp, english=en,
        registered_at=today,
        schedule=json.dumps(generate_schedule(today)),
        next_review_index=0, history="[]", completed=False,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify({"ok": True, "entry": entry.to_dict()})


@app.route("/api/bulk-add", methods=["POST"])
@login_required
def api_bulk_add():
    uid = session["user_id"]
    data = request.get_json()
    lines = data.get("lines", [])
    today = get_today_str()
    added = 0
    errors = []
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        if "/" not in line:
            errors.append(f"{i}行目: 「/」がありません")
            continue
        parts = line.split("/", 1)
        jp, en = parts[0].strip(), parts[1].strip()
        if not jp or not en:
            errors.append(f"{i}行目: 空欄があります")
            continue
        entry = Entry(
            user_id=uid, japanese=jp, english=en,
            registered_at=today,
            schedule=json.dumps(generate_schedule(today)),
            next_review_index=0, history="[]", completed=False,
        )
        db.session.add(entry)
        added += 1
    if added:
        db.session.commit()
    return jsonify({"ok": True, "count": added, "errors": errors})


@app.route("/api/answer", methods=["POST"])
@login_required
def api_answer():
    uid = session["user_id"]
    data = request.get_json()
    entry_id = data.get("id")
    answer = data.get("answer", "").strip()
    entry = Entry.query.filter_by(id=entry_id, user_id=uid).first()
    if not entry:
        return jsonify({"error": "not found"}), 404
    today = get_today_str()
    correct = answer.lower() == entry.english.lower()
    hist = json.loads(entry.history)
    sched = json.loads(entry.schedule)
    if correct:
        hist.append({"date": today, "result": "correct"})
        entry.next_review_index += 1
        if entry.next_review_index >= len(sched):
            entry.completed = True
    else:
        hist.append({"date": today, "result": "incorrect"})
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
        sched[entry.next_review_index] = tomorrow
        entry.schedule = json.dumps(sched)
    entry.history = json.dumps(hist)
    db.session.commit()
    return jsonify({
        "correct": correct,
        "expected": entry.english,
        "completed": entry.completed,
    })


@app.route("/api/edit", methods=["POST"])
@login_required
def api_edit():
    uid = session["user_id"]
    data = request.get_json()
    entry_id = data.get("id")
    jp = data.get("japanese", "").strip()
    en = data.get("english", "").strip()
    if not jp or not en:
        return jsonify({"error": "両方入力してください"}), 400
    entry = Entry.query.filter_by(id=entry_id, user_id=uid).first()
    if not entry:
        return jsonify({"error": "not found"}), 404
    entry.japanese = jp
    entry.english = en
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/delete", methods=["POST"])
@login_required
def api_delete():
    uid = session["user_id"]
    data = request.get_json()
    entry_id = data.get("id")
    entry = Entry.query.filter_by(id=entry_id, user_id=uid).first()
    if entry:
        db.session.delete(entry)
        db.session.commit()
    return jsonify({"ok": True})


# ===== HTML (シングルページアプリ) =====

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>英語学習ツール</title>
<style>
  :root {
    --bg: linear-gradient(135deg, #E0C3FC 0%, #8EC5FC 50%, #A1C4FD 100%);
    --card: #FFFFFF; --primary: #667EEA; --primary-hover: #5A67D8;
    --danger: #FC5C7D; --success: #43E97B; --success-dark: #2BB55A;
    --warning: #F6D365; --text: #2D3748; --text-light: #718096;
    --border: rgba(255,255,255,0.6); --shadow: 0 4px 20px rgba(0,0,0,0.08);
    --review-bg: linear-gradient(135deg, #FFF5F5, #FFFBEB);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Helvetica Neue", sans-serif;
    background: linear-gradient(135deg, #E0C3FC 0%, #8EC5FC 50%, #A1C4FD 100%);
    background-attachment: fixed; color: var(--text); min-height: 100vh;
  }
  .page { display: none; max-width: 700px; margin: 0 auto; padding: 0 20px 40px; }
  .page.active { display: block; }
  .header {
    background: rgba(255,255,255,0.25); backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    color: var(--text); padding: 14px 24px;
    margin: 0 -20px 24px; position: relative; text-align: center;
    min-height: 48px; display: flex; align-items: center; justify-content: center;
    border-bottom: 1px solid rgba(255,255,255,0.4);
  }
  .header h1 { font-size: 17px; font-weight: 700; }
  .back-btn {
    background: none; border: none; color: var(--text-light); font-size: 13px;
    cursor: pointer; padding: 4px 10px; border-radius: 6px;
    position: absolute; left: 16px; top: 50%; transform: translateY(-50%); width: auto;
  }
  .back-btn:hover { color: var(--text); }
  .title-bar {
    background: rgba(255,255,255,0.2); backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    text-align: center; padding: 32px 20px; margin: 0 -20px 24px;
    border-bottom: 1px solid rgba(255,255,255,0.3);
  }
  .title-bar h1 { font-size: 22px; font-weight: 800; line-height: 1.5; color: #FFFFFF; text-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  .user-bar {
    text-align: right; padding: 8px 0; font-size: 13px; color: var(--text-light);
  }
  .user-bar a { color: var(--primary); cursor: pointer; text-decoration: underline; margin-left: 8px; }
  .date { text-align: center; color: var(--text-light); font-size: 15px; margin-bottom: 14px; }
  .review-card {
    background: var(--card); border: none;
    border-radius: 20px; padding: 28px; text-align: center; margin-bottom: 16px;
    box-shadow: var(--shadow);
  }
  .review-card.has-reviews { background: linear-gradient(135deg, #FFF5F5, #FFFBEB); }
  .review-card .label { color: var(--text-light); font-size: 14px; }
  .review-card .count { font-size: 56px; font-weight: 800; line-height: 1.2; color: var(--success-dark); }
  .review-card.has-reviews .count { color: var(--danger); }
  .review-card .sub { color: var(--text-light); font-size: 14px; }
  .stats { display: flex; gap: 10px; margin-bottom: 20px; }
  .stat {
    flex: 1; background: var(--card); border: none;
    border-radius: 16px; padding: 14px; text-align: center; box-shadow: var(--shadow);
  }
  .stat .label { color: var(--text-light); font-size: 12px; }
  .stat .value { font-size: 24px; font-weight: 700; margin-top: 2px; color: var(--primary); }
  .btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .btn-full { grid-column: 1 / -1; }
  button, .btn {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 14px 20px; border: none; border-radius: 14px;
    font-size: 15px; font-weight: 700; cursor: pointer;
    transition: all 0.2s; width: 100%; box-shadow: 0 2px 10px rgba(0,0,0,0.08);
  }
  button:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.12); }
  .btn-primary { background: linear-gradient(135deg, #667EEA, #764BA2); color: #fff; }
  .btn-primary:hover { background: linear-gradient(135deg, #5A67D8, #6B46A0); }
  .btn-danger { background: linear-gradient(135deg, #FC5C7D, #F093FB); color: #fff; }
  .btn-danger:hover { background: linear-gradient(135deg, #E8506F, #E07EEA); }
  .btn-success { background: linear-gradient(135deg, #43E97B, #38F9D7); color: #fff; }
  .btn-success:hover { background: linear-gradient(135deg, #38D86C, #30E8C8); }
  .btn-secondary { background: var(--card); color: var(--text); }
  .btn-secondary:hover { background: #F7FAFC; }
  .btn-quit { margin-top: 4px; }
  .card {
    background: var(--card); border: none;
    border-radius: 20px; padding: 28px; margin-bottom: 16px; box-shadow: var(--shadow);
  }
  label { display: block; font-weight: 600; margin-bottom: 6px; font-size: 14px; color: var(--text-light); }
  input[type="text"], input[type="password"], textarea {
    width: 100%; padding: 12px 16px; border: 2px solid #E2E8F0;
    border-radius: 12px; font-size: 16px; font-family: inherit;
    background: #F7FAFC; color: var(--text); outline: none; transition: all 0.2s;
  }
  input[type="text"]:focus, input[type="password"]:focus, textarea:focus {
    border-color: var(--primary); background: #fff; box-shadow: 0 0 0 3px rgba(102,126,234,0.15);
  }
  textarea { resize: vertical; min-height: 180px; }
  .form-group { margin-bottom: 16px; }
  .input-with-btn { display: flex; gap: 8px; }
  .input-with-btn input { flex: 1; }
  .btn-search {
    white-space: nowrap; padding: 8px 14px; font-size: 13px; font-weight: 600;
    background: linear-gradient(135deg, #667EEA, #764BA2); color: #fff;
    border: none; border-radius: 12px; cursor: pointer; width: auto;
  }
  .btn-search:hover { opacity: 0.9; }
  .msg { padding: 12px 16px; border-radius: 12px; margin-top: 12px; font-size: 14px; }
  .msg-success { background: linear-gradient(135deg, #F0FFF4, #F0FCFF); color: var(--success-dark); border: 1px solid rgba(67,233,123,0.3); }
  .msg-error { background: #FEF2F2; color: var(--danger); border: 1px solid #FECACA; }
  .msg-warning { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; }
  .progress-bar { background: rgba(255,255,255,0.5); border-radius: 99px; height: 10px; margin: 8px 0 20px; }
  .progress-fill { background: linear-gradient(90deg, #667EEA, #764BA2); height: 100%; border-radius: 99px; transition: width 0.3s; }
  .question { font-size: 32px; font-weight: 700; text-align: center; padding: 20px 0; }
  .question-label { text-align: center; color: var(--text-light); font-size: 13px; }
  .answer-row { display: flex; gap: 8px; }
  .answer-row input { flex: 1; text-align: center; font-size: 18px; }
  .answer-row button { width: auto; min-width: 100px; }
  .feedback { text-align: center; font-size: 20px; font-weight: 700; padding: 16px 0; }
  .feedback.correct { color: var(--success-dark); }
  .feedback.incorrect { color: var(--danger); }
  .result-pct { font-size: 64px; font-weight: 800; text-align: center; }
  .result-detail { text-align: center; color: var(--text-light); font-size: 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { background: #F7FAFC; font-weight: 600; text-align: left; padding: 12px 14px; border-bottom: 2px solid #E2E8F0; }
  td { padding: 12px 14px; border-bottom: 1px solid #EDF2F7; }
  tr:hover td { background: #F7FAFC; }
  tr.selected td { background: #EBF4FF; }
  .status-badge { display: inline-block; padding: 3px 12px; border-radius: 99px; font-size: 12px; font-weight: 600; }
  .status-review { background: linear-gradient(135deg, #FFF5F5, #FFFBEB); color: #E53E3E; }
  .status-active { background: linear-gradient(135deg, #F0FFF4, #F0FCFF); color: var(--success-dark); }
  .status-done { background: #EDF2F7; color: var(--text-light); }
  .list-actions { display: flex; gap: 8px; margin-top: 14px; }
  .list-actions button { width: auto; }
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4);
    z-index: 100; align-items: center; justify-content: center;
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: var(--card); border-radius: 20px; padding: 28px;
    max-width: 440px; width: 90%; max-height: 80vh; overflow-y: auto;
    box-shadow: 0 8px 32px rgba(0,0,0,0.12);
  }
  .modal h2 { font-size: 18px; margin-bottom: 12px; }
  .modal .meta { color: var(--text-light); font-size: 13px; margin-bottom: 4px; }
  .hist-item { padding: 6px 0; font-size: 14px; }
  .hist-correct { color: var(--success-dark); }
  .hist-incorrect { color: var(--danger); }
  .btn-edit {
    padding: 4px 14px; font-size: 12px; font-weight: 600; width: auto;
    background: linear-gradient(135deg, #667EEA, #764BA2); color: #fff;
    border: none; border-radius: 8px; cursor: pointer; box-shadow: none;
  }
  .btn-edit:hover { opacity: 0.85; }
  .modal .form-group { margin-bottom: 14px; }
  .modal label { font-size: 14px; }
  .modal input[type="text"] { width: 100%; padding: 10px; font-size: 15px; }
  .empty { text-align: center; color: var(--text-light); padding: 40px 0; font-size: 16px; }
  /* 認証画面 */
  .auth-container {
    max-width: 400px; margin: 0 auto; padding: 60px 20px;
  }
  .auth-container .title-bar { margin: 0 0 24px; border-radius: 20px; }
  .auth-toggle { text-align: center; margin-top: 16px; font-size: 14px; color: var(--text-light); }
  .auth-toggle a { color: var(--primary); cursor: pointer; font-weight: 600; }
</style>
</head>
<body>

<!-- ===== ログイン ===== -->
<div id="pg-login" class="page">
  <div class="auth-container">
    <div class="title-bar">
      <h1>エビングハウスの忘却曲線<br>英語学習ツール</h1>
    </div>
    <div class="card">
      <div class="form-group">
        <label>ユーザー名</label>
        <input type="text" id="login-user" placeholder="ユーザー名を入力">
      </div>
      <div class="form-group">
        <label>パスワード</label>
        <input type="password" id="login-pass" placeholder="パスワードを入力">
      </div>
      <button class="btn-primary" onclick="doLogin()">ログイン</button>
      <div id="login-msg"></div>
    </div>
    <div class="auth-toggle">アカウントがない方は <a onclick="showPage('pg-register')">新規登録</a></div>
  </div>
</div>

<!-- ===== アカウント登録 ===== -->
<div id="pg-register" class="page">
  <div class="auth-container">
    <div class="title-bar">
      <h1>アカウント登録</h1>
    </div>
    <div class="card">
      <div class="form-group">
        <label>ユーザー名</label>
        <input type="text" id="reg-user" placeholder="2文字以上">
      </div>
      <div class="form-group">
        <label>パスワード</label>
        <input type="password" id="reg-pass" placeholder="4文字以上">
      </div>
      <div class="form-group">
        <label>パスワード（確認）</label>
        <input type="password" id="reg-pass2" placeholder="もう一度入力">
      </div>
      <button class="btn-success" onclick="doRegister()">登録する</button>
      <div id="reg-msg"></div>
    </div>
    <div class="auth-toggle">アカウントをお持ちの方は <a onclick="showPage('pg-login')">ログイン</a></div>
  </div>
</div>

<!-- ===== メニュー ===== -->
<div id="pg-menu" class="page">
  <div class="title-bar">
    <h1>エビングハウスの忘却曲線<br>英語学習ツール</h1>
  </div>
  <div class="user-bar" id="user-bar"></div>
  <div style="text-align:right;margin-bottom:4px"><a onclick="showPage('pg-howto')" style="color:var(--primary);cursor:pointer;font-size:13px;text-decoration:underline">使い方を見る</a></div>
  <div class="date" id="menu-date"></div>
  <div class="review-card" id="review-card">
    <div class="label">今日の復習</div>
    <div class="count" id="review-count">0</div>
    <div class="sub" id="review-sub"></div>
  </div>
  <div class="stats">
    <div class="stat"><div class="label">登録数</div><div class="value" id="st-total">0</div></div>
    <div class="stat"><div class="label">学習中</div><div class="value" id="st-active">0</div></div>
    <div class="stat"><div class="label">完了</div><div class="value" id="st-done">0</div></div>
  </div>
  <div class="btn-grid">
    <button class="btn-danger" id="btn-test">復習テスト</button>
    <button class="btn-success" onclick="showPage('pg-add')">新規登録</button>
    <button class="btn-secondary" onclick="showPage('pg-bulk')">一括登録</button>
    <button class="btn-secondary" onclick="loadList()">登録一覧</button>
  </div>
</div>

<!-- ===== 新規登録 ===== -->
<div id="pg-add" class="page">
  <div class="header">
    <button class="back-btn" onclick="goMenu()">← 戻る</button>
    <h1>新規登録</h1>
  </div>
  <div class="card">
    <div class="form-group">
      <label>日本語</label>
      <div class="input-with-btn">
        <input type="text" id="add-jp" placeholder="日本語を入力">
        <button class="btn-search" onclick="searchWeblio('add-jp')">日本語→英語で検索</button>
      </div>
    </div>
    <div class="form-group">
      <label>英語</label>
      <div class="input-with-btn">
        <input type="text" id="add-en" placeholder="英語を入力">
        <button class="btn-search" onclick="searchWeblio('add-en')">英語→日本語で検索</button>
      </div>
    </div>
    <button class="btn-success" onclick="addEntry()">登録する</button>
    <div id="add-msg"></div>
  </div>
</div>

<!-- ===== 一括登録 ===== -->
<div id="pg-bulk" class="page">
  <div class="header">
    <button class="back-btn" onclick="goMenu()">← 戻る</button>
    <h1>一括登録</h1>
  </div>
  <div class="card">
    <div class="form-group">
      <label>1行に「日本語 / 英語」の形式で入力</label>
      <textarea id="bulk-text" placeholder="例: こんにちは / Hello&#10;ありがとう / Thank you"></textarea>
    </div>
    <button class="btn-success" onclick="bulkAdd()">一括登録する</button>
    <div id="bulk-msg"></div>
  </div>
</div>

<!-- ===== 復習テスト ===== -->
<div id="pg-test" class="page">
  <div class="header">
    <button class="back-btn" onclick="confirmBack()">← 戻る</button>
    <h1>復習テスト</h1>
  </div>
  <div id="test-content"></div>
</div>

<!-- ===== 登録一覧 ===== -->
<div id="pg-list" class="page">
  <div class="header">
    <button class="back-btn" onclick="goMenu()">← 戻る</button>
    <h1>登録一覧</h1>
  </div>
  <div id="list-content"></div>
</div>

<!-- ===== 使い方 ===== -->
<div id="pg-howto" class="page">
  <div class="header">
    <button class="back-btn" onclick="goMenu()">← 戻る</button>
    <h1>使い方</h1>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin-bottom:8px">このアプリについて</h2>
    <p style="font-size:14px;line-height:1.8;color:var(--text-light)">
      エビングハウスの忘却曲線に基づいて、英単語・英語フレーズを効率的に記憶するための学習ツールです。
      登録した単語は <strong>1日後・3日後・7日後・14日後・30日後</strong> の計5回、最適なタイミングで復習テストが出題されます。
    </p>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin-bottom:8px">アカウント登録</h2>
    <p style="font-size:14px;line-height:1.8;color:var(--text-light)">
      初回はログイン画面の「新規登録」リンクからアカウントを作成してください。
      ユーザー名（2文字以上）とパスワード（4文字以上）を設定するだけで登録できます。
      次回以降は同じユーザー名とパスワードでログインしてください。
    </p>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin-bottom:8px">単語の登録方法</h2>
    <p style="font-size:14px;line-height:1.8;color:var(--text-light)">
      <strong>1. 新規登録（1件ずつ）</strong><br>
      メニューの「新規登録」ボタンから、日本語と英語のペアを登録します。
      意味がわからない単語や英訳を調べたいときは、入力欄の横にある「日本語→英語で検索」「英語→日本語で検索」ボタンを押すとWeblioの検索結果が開きます。
      検索結果から覚えたい単語や表現の日本語・英語をコピーして、それぞれの入力欄に貼り付けて登録できます。<br><br>
      <strong>2. 一括登録（まとめて）</strong><br>
      「一括登録」を使えば、複数の単語を一度に登録できます。
      1行に「日本語 / 英語」の形式で入力してください。
    </p>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin-bottom:8px">復習テストの使い方</h2>
    <p style="font-size:14px;line-height:1.8;color:var(--text-light)">
      <strong>1.</strong> メニュー画面に「今日の復習」の件数が表示されます。<br>
      <strong>2.</strong>「復習テスト」ボタンを押すと、日本語が表示されるので対応する英語を入力してください。<br>
      <strong>3.</strong> 正解するとスケジュールが次に進みます。不正解の場合は翌日に再出題されます。<br>
      <strong>4.</strong> 不正解の問題は、同じテスト内で全問正解するまで繰り返し出題されます。<br>
      <strong>5.</strong> テスト終了後に「もう一度復習する」で追加練習もできます（記録には影響しません）。
    </p>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin-bottom:8px">忘却曲線とは？</h2>
    <p style="font-size:14px;line-height:1.8;color:var(--text-light)">
      ドイツの心理学者ヘルマン・エビングハウスが発見した、記憶と時間の関係を示す曲線です。
      人は学習した内容を時間とともに忘れていきますが、適切なタイミングで復習することで記憶の定着率が大幅に向上します。<br><br>
      このアプリでは「1日後 → 3日後 → 7日後 → 14日後 → 30日後」の間隔で復習を行い、
      少ない回数で長期記憶への定着を目指します。5回の復習をすべてクリアすると「完了」となります。
    </p>
  </div>
</div>

<!-- ===== 履歴モーダル ===== -->
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal-body"></div>
</div>

<script>
const API = '';
let appData = null;
let testState = null;
let practiceState = null;
let selectedId = null;
let currentUser = null;

// --- ナビゲーション ---
function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
async function goMenu() {
  await loadMenu();
  showPage('pg-menu');
}

// --- 認証 ---
async function checkAuth() {
  const r = await fetch(API + '/api/me');
  const d = await r.json();
  if (d.logged_in) {
    currentUser = d.username;
    goMenu();
  } else {
    showPage('pg-login');
  }
}

async function doLogin() {
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value.trim();
  const msg = document.getElementById('login-msg');
  if (!user || !pass) { msg.className='msg msg-error'; msg.textContent='ユーザー名とパスワードを入力してください'; return; }
  const r = await fetch(API+'/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:user, password:pass})});
  const d = await r.json();
  if (d.ok) { currentUser = d.username; goMenu(); }
  else { msg.className='msg msg-error'; msg.textContent=d.error; }
}

async function doRegister() {
  const user = document.getElementById('reg-user').value.trim();
  const pass = document.getElementById('reg-pass').value.trim();
  const pass2 = document.getElementById('reg-pass2').value.trim();
  const msg = document.getElementById('reg-msg');
  if (!user || !pass) { msg.className='msg msg-error'; msg.textContent='全項目を入力してください'; return; }
  if (pass !== pass2) { msg.className='msg msg-error'; msg.textContent='パスワードが一致しません'; return; }
  const r = await fetch(API+'/api/register', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:user, password:pass})});
  const d = await r.json();
  if (d.ok) { currentUser = d.username; goMenu(); }
  else { msg.className='msg msg-error'; msg.textContent=d.error; }
}

async function doLogout() {
  await fetch(API+'/api/logout', {method:'POST'});
  currentUser = null;
  showPage('pg-login');
}

// --- メニュー ---
async function loadMenu() {
  const r = await fetch(API + '/api/data');
  if (r.status === 401) { showPage('pg-login'); return; }
  appData = await r.json();
  const d = new Date(appData.today);
  document.getElementById('menu-date').textContent =
    `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日`;
  document.getElementById('user-bar').innerHTML =
    `${esc(currentUser)} さん <a onclick="doLogout()">ログアウト</a>`;
  const rc = appData.review_count;
  document.getElementById('review-count').textContent = rc;
  const card = document.getElementById('review-card');
  card.className = 'review-card' + (rc > 0 ? ' has-reviews' : '');
  document.getElementById('review-sub').textContent =
    rc > 0 ? '件 — 復習しましょう！' : '件 — すべて完了！';
  document.getElementById('st-total').textContent = appData.total;
  document.getElementById('st-active').textContent = appData.active;
  document.getElementById('st-done').textContent = appData.completed;
  const btn = document.getElementById('btn-test');
  btn.textContent = rc > 0 ? `復習テスト (${rc}件)` : '復習テスト';
  btn.onclick = () => startTest();
}

// --- 新規登録 ---
async function addEntry() {
  const jp = document.getElementById('add-jp').value.trim();
  const en = document.getElementById('add-en').value.trim();
  const msg = document.getElementById('add-msg');
  if (!jp || !en) { msg.className='msg msg-error'; msg.textContent='日本語と英語の両方を入力してください'; return; }
  const r = await fetch(API+'/api/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({japanese:jp, english:en})});
  const d = await r.json();
  if (d.ok) {
    msg.className='msg msg-success';
    msg.textContent=`「${jp} = ${en}」を登録しました！ (ID: ${d.entry.id})`;
    document.getElementById('add-jp').value='';
    document.getElementById('add-en').value='';
    document.getElementById('add-jp').focus();
  }
}

// --- 一括登録 ---
async function bulkAdd() {
  const text = document.getElementById('bulk-text').value.trim();
  const msg = document.getElementById('bulk-msg');
  if (!text) { msg.className='msg msg-error'; msg.textContent='テキストを入力してください'; return; }
  const lines = text.split('\n');
  const r = await fetch(API+'/api/bulk-add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lines})});
  const d = await r.json();
  let parts = [];
  if (d.count > 0) parts.push(`${d.count}件を登録しました！`);
  if (d.errors.length > 0) parts.push(d.errors.join('\n'));
  if (d.count > 0 && d.errors.length === 0) { msg.className='msg msg-success'; document.getElementById('bulk-text').value=''; }
  else if (d.count > 0) msg.className='msg msg-warning';
  else msg.className='msg msg-error';
  msg.textContent = parts.join('\n');
}

// --- 復習テスト ---
async function startTest() {
  const r = await fetch(API+'/api/reviews');
  const d = await r.json();
  if (d.reviews.length === 0) {
    let html = '<div class="empty">今日復習するエントリーはありません</div>';
    html += '<button class="btn-primary" onclick="goMenu()" style="margin-top:16px">メニューに戻る</button>';
    html += '<button class="btn-secondary" onclick="startPractice()" style="margin-top:8px">今日の復習をもう一度練習する</button>';
    html += '<div style="color:var(--text-light);font-size:12px;margin-top:6px;text-align:center">※ 追加練習（記録に影響しません）</div>';
    document.getElementById('test-content').innerHTML = html;
    showPage('pg-test');
    return;
  }
  testState = {
    reviews: d.reviews, idx: 0, correct: 0, tested: 0, waiting: false,
    incorrect: [], round: 1, totalOriginal: d.reviews.length
  };
  showPage('pg-test');
  renderQuestion();
}

function renderQuestion() {
  const s = testState;
  const entry = s.reviews[s.idx];
  const total = s.reviews.length;
  const pct = ((s.idx) / total * 100).toFixed(0);
  const roundLabel = s.round > 1 ? `<div style="color:var(--danger);font-size:13px;font-weight:600;text-align:center;margin-bottom:4px">再テスト（${s.round}回目）</div>` : '';
  document.getElementById('test-content').innerHTML = `
    ${roundLabel}
    <div style="color:var(--text-light);font-size:14px;text-align:center">問題 ${s.idx+1} / ${total}</div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="card">
      <div class="question-label">日本語</div>
      <div class="question">${esc(entry.japanese)}</div>
      <div class="question-label">英語を入力してください</div>
      <div class="answer-row" style="margin-top:8px">
        <input type="text" id="answer-input" autocomplete="off" autofocus>
        <button class="btn-primary" id="submit-btn" onclick="submitAnswer()">回答</button>
      </div>
    </div>
    <div class="feedback" id="feedback"></div>
  `;
  const inp = document.getElementById('answer-input');
  function handleKey(e) { if (e.key === 'Enter') { e.preventDefault(); submitAnswer(); } }
  inp.addEventListener('keydown', handleKey);
  document.addEventListener('keydown', testState._keyHandler = handleKey);
  setTimeout(() => inp.focus(), 50);
}

async function submitAnswer() {
  const s = testState;
  if (s.waiting) { nextQuestion(); return; }
  const inp = document.getElementById('answer-input');
  const answer = inp.value.trim();
  if (!answer) return;
  const entry = s.reviews[s.idx];
  s.tested++;
  const r = await fetch(API+'/api/answer', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:entry.id, answer})});
  const d = await r.json();
  const fb = document.getElementById('feedback');
  if (d.correct) {
    s.correct++;
    fb.className = 'feedback correct';
    fb.textContent = '○ 正解！' + (d.completed ? '  （全復習完了！）' : '');
  } else {
    s.incorrect.push(entry);
    fb.className = 'feedback incorrect';
    fb.textContent = `× 不正解　正解: ${d.expected}`;
  }
  inp.disabled = true;
  s.waiting = true;
  document.getElementById('submit-btn').textContent = '次へ';
}

function nextQuestion() {
  const s = testState;
  if (s._keyHandler) { document.removeEventListener('keydown', s._keyHandler); s._keyHandler = null; }
  s.idx++;
  s.waiting = false;
  if (s.idx >= s.reviews.length) {
    if (s.incorrect.length === 0) { showFinalResult(); }
    else { showRoundResult(); }
    return;
  }
  renderQuestion();
}

function showRoundResult() {
  const s = testState;
  const roundCorrect = s.reviews.length - s.incorrect.length;
  document.getElementById('test-content').innerHTML = `
    <div class="card" style="text-align:center;padding:32px">
      <h2 style="margin-bottom:12px">${s.round === 1 ? '1回目' : s.round + '回目'}の結果</h2>
      <div style="font-size:20px;font-weight:700;color:var(--primary);margin-bottom:4px">${roundCorrect} / ${s.reviews.length} 正解</div>
      <div style="color:var(--danger);font-size:16px;margin-bottom:20px">不正解 ${s.incorrect.length}件 → 再テストします</div>
      <button class="btn-danger" onclick="startRetry()">不正解の問題を再テスト</button>
    </div>
  `;
}

function startRetry() {
  const s = testState;
  s.reviews = s.incorrect;
  s.incorrect = [];
  s.idx = 0;
  s.round++;
  renderQuestion();
}

function showFinalResult() {
  const s = testState;
  const perfect = s.round === 1;
  document.getElementById('test-content').innerHTML = `
    <div class="card" style="text-align:center;padding:40px">
      <h2 style="margin-bottom:16px">${perfect ? 'テスト結果' : '再テスト完了'}</h2>
      <div class="result-pct" style="color:var(--success-dark)">完璧です！</div>
      <div class="result-detail" style="margin-top:8px">${s.totalOriginal}問すべて正解</div>
      ${!perfect ? `<div style="color:var(--text-light);font-size:14px;margin-top:4px">(${s.round}回目で全問正解)</div>` : ''}
      <button class="btn-primary" onclick="goMenu()" style="margin-top:24px">メニューに戻る</button>
      <button class="btn-secondary" onclick="startPractice()" style="margin-top:8px">もう一度復習する</button>
      <div style="color:var(--text-light);font-size:12px;margin-top:6px">※ 追加練習（記録に影響しません）</div>
    </div>
  `;
}

function confirmBack() {
  if (testState && testState.tested > 0) {
    if (!confirm('テストを中断してメニューに戻りますか？')) return;
  }
  if (testState && testState._keyHandler) document.removeEventListener('keydown', testState._keyHandler);
  if (practiceState && practiceState._keyHandler) document.removeEventListener('keydown', practiceState._keyHandler);
  testState = null;
  practiceState = null;
  goMenu();
}

// --- 追加練習モード ---
async function startPractice() {
  const r = await fetch(API+'/api/today-practiced');
  const d = await r.json();
  if (d.entries.length === 0) {
    document.getElementById('test-content').innerHTML = '<div class="empty">今日練習した問題がありません</div><button class="btn-primary" onclick="goMenu()" style="margin-top:16px">メニューに戻る</button>';
    showPage('pg-test');
    return;
  }
  const shuffled = [...d.entries].sort(() => Math.random() - 0.5);
  practiceState = { reviews: shuffled, idx: 0, correct: 0, total: shuffled.length, waiting: false, incorrect: [] };
  testState = { tested: 1, practice: true };
  showPage('pg-test');
  renderPracticeQuestion();
}

function renderPracticeQuestion() {
  const s = practiceState;
  const entry = s.reviews[s.idx];
  const pct = (s.idx / s.total * 100).toFixed(0);
  document.getElementById('test-content').innerHTML = `
    <div style="color:var(--warning);font-size:13px;font-weight:600;text-align:center;margin-bottom:4px">追加練習（記録なし）</div>
    <div style="color:var(--text-light);font-size:14px;text-align:center">問題 ${s.idx+1} / ${s.total}</div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%;background:var(--warning)"></div></div>
    <div class="card">
      <div class="question-label">日本語</div>
      <div class="question">${esc(entry.japanese)}</div>
      <div class="question-label">英語を入力してください</div>
      <div class="answer-row" style="margin-top:8px">
        <input type="text" id="answer-input" autocomplete="off" autofocus>
        <button class="btn-primary" id="submit-btn" onclick="submitPracticeAnswer()">回答</button>
      </div>
    </div>
    <div class="feedback" id="feedback"></div>
  `;
  const inp = document.getElementById('answer-input');
  function handleKey(e) { if (e.key === 'Enter') { e.preventDefault(); submitPracticeAnswer(); } }
  inp.addEventListener('keydown', handleKey);
  document.addEventListener('keydown', practiceState._keyHandler = handleKey);
  setTimeout(() => inp.focus(), 50);
}

function submitPracticeAnswer() {
  const s = practiceState;
  if (s.waiting) { nextPracticeQuestion(); return; }
  const inp = document.getElementById('answer-input');
  const answer = inp.value.trim();
  if (!answer) return;
  const entry = s.reviews[s.idx];
  const correct = answer.toLowerCase() === entry.english.toLowerCase();
  const fb = document.getElementById('feedback');
  if (correct) {
    s.correct++;
    fb.className = 'feedback correct';
    fb.textContent = '○ 正解！';
  } else {
    s.incorrect.push(entry);
    fb.className = 'feedback incorrect';
    fb.textContent = `× 不正解　正解: ${entry.english}`;
  }
  inp.disabled = true;
  s.waiting = true;
  document.getElementById('submit-btn').textContent = '次へ';
}

function nextPracticeQuestion() {
  const s = practiceState;
  if (s._keyHandler) { document.removeEventListener('keydown', s._keyHandler); s._keyHandler = null; }
  s.idx++;
  s.waiting = false;
  if (s.idx >= s.total) { showPracticeResult(); return; }
  renderPracticeQuestion();
}

function showPracticeResult() {
  const s = practiceState;
  const pct = Math.round(s.correct / s.total * 100);
  let wrongHtml = '';
  if (s.incorrect.length > 0) {
    wrongHtml = `<div style="text-align:left;margin-top:20px;border-top:1px solid #E2E8F0;padding-top:16px">
      <div style="font-weight:700;font-size:14px;color:var(--danger);margin-bottom:10px">不正解（${s.incorrect.length}件）</div>`;
    s.incorrect.forEach(e => {
      wrongHtml += `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #EDF2F7;font-size:14px">
        <span style="color:var(--text)">${esc(e.japanese)}</span>
        <span style="color:var(--primary);font-weight:600">${esc(e.english)}</span>
      </div>`;
    });
    wrongHtml += '</div>';
  }
  document.getElementById('test-content').innerHTML = `
    <div class="card" style="text-align:center;padding:40px">
      <div style="color:var(--warning);font-size:13px;font-weight:600;margin-bottom:8px">追加練習（記録なし）</div>
      <h2 style="margin-bottom:16px">練習結果</h2>
      <div class="result-pct" style="color:${pct===100?'var(--success-dark)':'var(--primary)'}">${pct}%</div>
      <div class="result-detail" style="margin-top:8px">${s.total}問中 ${s.correct}問正解</div>
      ${wrongHtml}
      <button class="btn-primary" onclick="goMenu()" style="margin-top:24px">メニューに戻る</button>
      <button class="btn-secondary" onclick="startPractice()" style="margin-top:8px">もう一度練習する</button>
    </div>
  `;
  practiceState = null;
  testState = null;
}

// --- 登録一覧 ---
async function loadList() {
  const r = await fetch(API+'/api/data');
  appData = await r.json();
  selectedId = null;
  renderList();
  showPage('pg-list');
}

function renderList() {
  const entries = [...appData.entries].sort((a, b) => b.id - a.id);
  if (entries.length === 0) {
    document.getElementById('list-content').innerHTML = '<div class="empty">登録されているエントリーはありません</div>';
    return;
  }
  const today = appData.today;
  let html = '<div class="card" style="padding:0;overflow:hidden;border-radius:20px"><table><thead><tr><th>ID</th><th>日本語</th><th>英語</th><th>状態</th><th>次回復習</th><th></th></tr></thead><tbody>';
  entries.forEach(e => {
    let status, badge, next;
    if (e.completed) {
      status = '完了'; badge = 'status-done'; next = '-';
    } else {
      const idx = e.next_review_index;
      next = idx < e.schedule.length ? e.schedule[idx] : '-';
      if (next <= today) { status = '要復習'; badge = 'status-review'; }
      else { status = `学習中(${idx}/5)`; badge = 'status-active'; }
    }
    const sel = e.id === selectedId ? ' selected' : '';
    html += `<tr class="${sel}" onclick="selectEntry(${e.id})" style="cursor:pointer">
      <td>${e.id}</td><td>${esc(e.japanese)}</td><td>${esc(e.english)}</td>
      <td><span class="status-badge ${badge}">${status}</span></td><td>${next}</td>
      <td><button class="btn-edit" onclick="event.stopPropagation();openEdit(${e.id})">編集</button></td></tr>`;
  });
  html += '</tbody></table></div>';
  html += `<div class="list-actions">
    <button class="btn-primary" onclick="showHistory()">学習履歴</button>
    <button class="btn-danger" onclick="deleteEntry()">削除</button>
  </div>`;
  document.getElementById('list-content').innerHTML = html;
}

function selectEntry(id) { selectedId = id; renderList(); }

function showHistory() {
  if (!selectedId) { alert('エントリーを選択してください'); return; }
  const e = appData.entries.find(x => x.id === selectedId);
  if (!e) return;
  let hist = '';
  if (e.history.length === 0) hist = '<div style="color:var(--text-light);padding:8px 0">まだ学習履歴はありません</div>';
  else e.history.forEach(h => {
    const cls = h.result === 'correct' ? 'hist-correct' : 'hist-incorrect';
    const mark = h.result === 'correct' ? '○ 正解' : '× 不正解';
    hist += `<div class="hist-item ${cls}">${h.date}　${mark}</div>`;
  });
  const status = e.completed ? '完了' : '学習中';
  document.getElementById('modal-body').innerHTML = `
    <h2>${esc(e.japanese)}　=　${esc(e.english)}</h2>
    <div class="meta">登録日: ${e.registered_at}</div>
    <div class="meta">状態: ${status}</div>
    <div class="meta" style="margin-bottom:12px">スケジュール: ${e.schedule.join(' → ')}</div>
    <h3 style="font-size:15px;margin-bottom:8px">学習履歴</h3>
    ${hist}
    <button class="btn-secondary" onclick="closeModal()" style="margin-top:16px">閉じる</button>
  `;
  document.getElementById('modal-overlay').classList.add('active');
}

async function deleteEntry() {
  if (!selectedId) { alert('エントリーを選択してください'); return; }
  const e = appData.entries.find(x => x.id === selectedId);
  if (!e) return;
  if (!confirm(`「${e.japanese} = ${e.english}」を削除しますか？`)) return;
  await fetch(API+'/api/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:selectedId})});
  selectedId = null;
  await loadList();
}

function openEdit(id) {
  const e = appData.entries.find(x => x.id === id);
  if (!e) return;
  document.getElementById('modal-body').innerHTML = `
    <h2>エントリーを編集</h2>
    <div class="form-group">
      <label>日本語</label>
      <input type="text" id="edit-jp" value="${esc(e.japanese).replace(/"/g,'&quot;')}">
    </div>
    <div class="form-group">
      <label>英語</label>
      <input type="text" id="edit-en" value="${esc(e.english).replace(/"/g,'&quot;')}">
    </div>
    <div id="edit-msg"></div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="btn-primary" onclick="saveEdit(${id})" style="flex:1">保存</button>
      <button class="btn-secondary" onclick="closeModal()" style="flex:1">キャンセル</button>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('active');
  setTimeout(() => document.getElementById('edit-jp').focus(), 50);
}

async function saveEdit(id) {
  const jp = document.getElementById('edit-jp').value.trim();
  const en = document.getElementById('edit-en').value.trim();
  const msg = document.getElementById('edit-msg');
  if (!jp || !en) { msg.className='msg msg-error'; msg.textContent='日本語と英語の両方を入力してください'; return; }
  const r = await fetch(API+'/api/edit', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, japanese:jp, english:en})});
  const d = await r.json();
  if (d.ok) { closeModal(); await loadList(); }
  else { msg.className='msg msg-error'; msg.textContent=d.error||'エラーが発生しました'; }
}

function closeModal() { document.getElementById('modal-overlay').classList.remove('active'); }

// --- Weblio ---
function searchWeblio(inputId) {
  const val = document.getElementById(inputId).value.trim();
  const msg = document.getElementById('add-msg');
  if (!val) { msg.className='msg msg-error'; msg.textContent='入力してください'; return; }
  msg.textContent = ''; msg.className = '';
  window.open('https://ejje.weblio.jp/content/' + encodeURIComponent(val), '_blank');
}

// --- ユーティリティ ---
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// --- Enterキー ---
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('add-en').addEventListener('keydown', e => { if(e.key==='Enter') addEntry(); });
  document.getElementById('login-pass').addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); });
  document.getElementById('reg-pass2').addEventListener('keydown', e => { if(e.key==='Enter') doRegister(); });
  checkAuth();
});
</script>
</body>
</html>
"""


# ===== DB初期化 + 起動 =====

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
