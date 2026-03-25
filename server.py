import os
import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
import jwt
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brainsprouts.db")

SKIP_DATES = {"2026-04-29", "2026-05-06", "2026-05-13"}
MAX_CAPACITY = 15

# ── Database helpers ──────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS rsvps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('yes','maybe','no')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, event_date)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Seed admin account if not exists
    existing = db.execute("SELECT id FROM users WHERE username = ?", ("AndyAlbert",)).fetchone()
    if not existing:
        pw_hash = bcrypt.hashpw("BrainSprouts2000".encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
            ("AndyAlbert", pw_hash, "Andy Albert"),
        )
    db.commit()
    db.close()

# ── Auth helpers ──────────────────────────────────────────────────────────────

def create_token(user_id, is_admin):
    return jwt.encode(
        {"user_id": user_id, "is_admin": bool(is_admin), "exp": datetime.utcnow() + timedelta(hours=12)},
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token required"}), 401
        try:
            data = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        g.user_id = data["user_id"]
        g.is_admin = data["is_admin"]
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if not g.is_admin:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

# ── Event generation ──────────────────────────────────────────────────────────

def get_wednesday_events():
    events = []
    start = datetime(2026, 3, 25)
    end = datetime(2026, 6, 30)
    current = start
    # Advance to first Wednesday
    while current.weekday() != 2:
        current += timedelta(days=1)
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        if date_str not in SKIP_DATES:
            events.append(date_str)
        current += timedelta(days=7)
    return events

# ── Routes: Static ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "login.html")

@app.route("/dashboard")
def dashboard():
    return send_from_directory("templates", "dashboard.html")

@app.route("/admin")
def admin_page():
    return send_from_directory("templates", "admin.html")

# ── Routes: Auth ──────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_token(user["id"], user["is_admin"])
    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "is_admin": bool(user["is_admin"]),
        },
    })

# ── Routes: Admin ─────────────────────────────────────────────────────────────

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def list_users():
    db = get_db()
    users = db.execute("SELECT id, username, display_name, is_admin, created_at FROM users ORDER BY display_name").fetchall()
    return jsonify([dict(u) for u in users])

@app.route("/api/admin/users", methods=["POST"])
@admin_required
def create_user():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    display_name = data.get("display_name", "").strip()
    if not username or not password or not display_name:
        return jsonify({"error": "All fields required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Username already exists"}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.execute(
        "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
        (username, pw_hash, display_name),
    )
    db.commit()
    return jsonify({"message": "User created successfully"}), 201

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    db = get_db()
    db.execute("DELETE FROM rsvps WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    db.commit()
    return jsonify({"message": "User deleted"})

@app.route("/api/admin/events", methods=["GET"])
@admin_required
def admin_events():
    db = get_db()
    events = get_wednesday_events()
    result = []
    for date in events:
        rsvps = db.execute("""
            SELECT u.display_name, u.username, r.status
            FROM rsvps r JOIN users u ON r.user_id = u.id
            WHERE r.event_date = ?
            ORDER BY r.status, u.display_name
        """, (date,)).fetchall()
        yes_count = sum(1 for r in rsvps if r["status"] == "yes")
        maybe_count = sum(1 for r in rsvps if r["status"] == "maybe")
        no_count = sum(1 for r in rsvps if r["status"] == "no")
        result.append({
            "date": date,
            "yes_count": yes_count,
            "maybe_count": maybe_count,
            "no_count": no_count,
            "at_capacity": yes_count >= MAX_CAPACITY,
            "rsvps": [dict(r) for r in rsvps],
        })
    return jsonify(result)

# ── Routes: Announcements ─────────────────────────────────────────────────────

@app.route("/api/announcements", methods=["GET"])
@token_required
def get_announcements():
    db = get_db()
    rows = db.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/admin/announcements", methods=["POST"])
@admin_required
def create_announcement():
    data = request.get_json()
    title = data.get("title", "").strip()
    body = data.get("body", "").strip()
    if not title or not body:
        return jsonify({"error": "Title and body required"}), 400
    db = get_db()
    db.execute("INSERT INTO announcements (title, body) VALUES (?, ?)", (title, body))
    db.commit()
    return jsonify({"message": "Announcement posted"}), 201

@app.route("/api/admin/announcements/<int:ann_id>", methods=["DELETE"])
@admin_required
def delete_announcement(ann_id):
    db = get_db()
    db.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
    db.commit()
    return jsonify({"message": "Announcement deleted"})

# ── Routes: Calendar / RSVP ──────────────────────────────────────────────────

@app.route("/api/events", methods=["GET"])
@token_required
def get_events():
    db = get_db()
    events = get_wednesday_events()
    my_rsvps = db.execute("SELECT event_date, status FROM rsvps WHERE user_id = ?", (g.user_id,)).fetchall()
    rsvp_map = {r["event_date"]: r["status"] for r in my_rsvps}

    result = []
    for date in events:
        yes_count = db.execute(
            "SELECT COUNT(*) as c FROM rsvps WHERE event_date = ? AND status = 'yes'", (date,)
        ).fetchone()["c"]
        result.append({
            "date": date,
            "my_status": rsvp_map.get(date),
            "yes_count": yes_count,
            "at_capacity": yes_count >= MAX_CAPACITY,
        })
    return jsonify(result)

@app.route("/api/rsvp", methods=["POST"])
@token_required
def set_rsvp():
    data = request.get_json()
    event_date = data.get("date", "")
    status = data.get("status", "")
    if event_date not in get_wednesday_events():
        return jsonify({"error": "Invalid event date"}), 400
    if status not in ("yes", "maybe", "no"):
        return jsonify({"error": "Status must be yes, maybe, or no"}), 400

    db = get_db()

    # Check capacity if trying to RSVP yes
    if status == "yes":
        yes_count = db.execute(
            "SELECT COUNT(*) as c FROM rsvps WHERE event_date = ? AND status = 'yes' AND user_id != ?",
            (event_date, g.user_id),
        ).fetchone()["c"]
        if yes_count >= MAX_CAPACITY:
            return jsonify({"error": "Event is at full capacity (15/15)"}), 409

    db.execute("""
        INSERT INTO rsvps (user_id, event_date, status, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, event_date) DO UPDATE SET status = ?, updated_at = datetime('now')
    """, (g.user_id, event_date, status, status))
    db.commit()
    return jsonify({"message": "RSVP updated"})

# ── Main ──────────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    print("BrainSprouts Tutor Management running at http://localhost:5000")
    app.run(debug=True, port=5000)
