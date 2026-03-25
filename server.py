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
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras

SKIP_DATES = {"2026-04-29", "2026-05-06", "2026-05-13"}
MAX_CAPACITY = 15
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brainsprouts.db")

# ── Database helpers ──────────────────────────────────────────────────────────

class DictRow(dict):
    """Make sqlite3.Row results accessible like dicts."""
    pass

def _sqlite_dict_factory(cursor, row):
    d = DictRow()
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_db():
    if "db" not in g:
        if USE_PG:
            g.db = psycopg2.connect(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = _sqlite_dict_factory
    return g.db

def get_cursor():
    db = get_db()
    if USE_PG:
        return db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return db.cursor()

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def _param(sql):
    """Convert %s placeholders to ? for SQLite."""
    if USE_PG:
        return sql
    return sql.replace("%s", "?")

def init_db():
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rsvps (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                event_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('yes','maybe','no')),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, event_date)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("SELECT id FROM users WHERE username = %s", ("AndyAlbert",))
        if not cur.fetchone():
            pw_hash = bcrypt.hashpw("BrainSprouts2000".encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (%s, %s, %s, TRUE)",
                ("AndyAlbert", pw_hash, "Andy Albert"),
            )
    else:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("SELECT id FROM users WHERE username = ?", ("AndyAlbert",))
        if not cur.fetchone():
            pw_hash = bcrypt.hashpw("BrainSprouts2000".encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
                ("AndyAlbert", pw_hash, "Andy Albert"),
            )
    conn.commit()
    cur.close()
    conn.close()

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

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_expr():
    return "NOW()" if USE_PG else "datetime('now')"

def serialize_row(row):
    """Ensure datetime fields are strings for JSON."""
    d = dict(row)
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if val and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    return d

# ── Event generation ──────────────────────────────────────────────────────────

def get_wednesday_events():
    events = []
    start = datetime(2026, 3, 25)
    end = datetime(2026, 6, 30)
    current = start
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

    cur = get_cursor()
    cur.execute(_param("SELECT * FROM users WHERE username = %s"), (username,))
    user = cur.fetchone()
    cur.close()
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
    cur = get_cursor()
    cur.execute("SELECT id, username, display_name, is_admin, created_at FROM users ORDER BY display_name")
    users = cur.fetchall()
    cur.close()
    return jsonify([serialize_row(u) for u in users])

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

    cur = get_cursor()
    cur.execute(_param("SELECT id FROM users WHERE username = %s"), (username,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "Username already exists"}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        _param("INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)"),
        (username, pw_hash, display_name),
    )
    get_db().commit()
    cur.close()
    return jsonify({"message": "User created successfully"}), 201

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    cur = get_cursor()
    cur.execute(_param("DELETE FROM rsvps WHERE user_id = %s"), (user_id,))
    if USE_PG:
        cur.execute(_param("DELETE FROM users WHERE id = %s AND is_admin = FALSE"), (user_id,))
    else:
        cur.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "User deleted"})

@app.route("/api/admin/events", methods=["GET"])
@admin_required
def admin_events():
    cur = get_cursor()
    events = get_wednesday_events()
    result = []
    for date in events:
        cur.execute(_param("""
            SELECT u.display_name, u.username, r.status
            FROM rsvps r JOIN users u ON r.user_id = u.id
            WHERE r.event_date = %s
            ORDER BY r.status, u.display_name
        """), (date,))
        rsvps = cur.fetchall()
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
    cur.close()
    return jsonify(result)

# ── Routes: Announcements ─────────────────────────────────────────────────────

@app.route("/api/announcements", methods=["GET"])
@token_required
def get_announcements():
    cur = get_cursor()
    cur.execute("SELECT * FROM announcements ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route("/api/admin/announcements", methods=["POST"])
@admin_required
def create_announcement():
    data = request.get_json()
    title = data.get("title", "").strip()
    body = data.get("body", "").strip()
    if not title or not body:
        return jsonify({"error": "Title and body required"}), 400
    cur = get_cursor()
    cur.execute(_param("INSERT INTO announcements (title, body) VALUES (%s, %s)"), (title, body))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Announcement posted"}), 201

@app.route("/api/admin/announcements/<int:ann_id>", methods=["DELETE"])
@admin_required
def delete_announcement(ann_id):
    cur = get_cursor()
    cur.execute(_param("DELETE FROM announcements WHERE id = %s"), (ann_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Announcement deleted"})

# ── Routes: Calendar / RSVP ──────────────────────────────────────────────────

@app.route("/api/events", methods=["GET"])
@token_required
def get_events():
    cur = get_cursor()
    events = get_wednesday_events()
    cur.execute(_param("SELECT event_date, status FROM rsvps WHERE user_id = %s"), (g.user_id,))
    my_rsvps = cur.fetchall()
    rsvp_map = {r["event_date"]: r["status"] for r in my_rsvps}

    result = []
    for date in events:
        cur.execute(
            _param("SELECT COUNT(*) as c FROM rsvps WHERE event_date = %s AND status = 'yes'"), (date,)
        )
        yes_count = cur.fetchone()["c"]
        result.append({
            "date": date,
            "my_status": rsvp_map.get(date),
            "yes_count": yes_count,
            "at_capacity": yes_count >= MAX_CAPACITY,
        })
    cur.close()
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

    cur = get_cursor()

    if status == "yes":
        cur.execute(
            _param("SELECT COUNT(*) as c FROM rsvps WHERE event_date = %s AND status = 'yes' AND user_id != %s"),
            (event_date, g.user_id),
        )
        if cur.fetchone()["c"] >= MAX_CAPACITY:
            cur.close()
            return jsonify({"error": "Event is at full capacity (15/15)"}), 409

    nw = now_expr()
    if USE_PG:
        cur.execute(f"""
            INSERT INTO rsvps (user_id, event_date, status, updated_at)
            VALUES (%s, %s, %s, {nw})
            ON CONFLICT(user_id, event_date) DO UPDATE SET status = %s, updated_at = {nw}
        """, (g.user_id, event_date, status, status))
    else:
        cur.execute(f"""
            INSERT INTO rsvps (user_id, event_date, status, updated_at)
            VALUES (?, ?, ?, {nw})
            ON CONFLICT(user_id, event_date) DO UPDATE SET status = ?, updated_at = {nw}
        """, (g.user_id, event_date, status, status))
    get_db().commit()
    cur.close()
    return jsonify({"message": "RSVP updated"})

# ── Main ──────────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    mode = "PostgreSQL" if USE_PG else "SQLite (local)"
    print(f"BrainSprouts Tutor Management running at http://localhost:5000 [{mode}]")
    app.run(debug=True, port=5000)
