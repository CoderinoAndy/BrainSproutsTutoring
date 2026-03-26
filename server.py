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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brainsprouts.db")

# ── Database helpers ──────────────────────────────────────────────────────────

class DictRow(dict):
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

def _p(sql):
    """Convert %s placeholders to ? for SQLite."""
    if USE_PG:
        return sql
    return sql.replace("%s", "?")

def now_expr():
    return "NOW()" if USE_PG else "datetime('now')"

def serialize_row(row):
    d = dict(row)
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if val and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    return d

# ── Seed Wednesday events ─────────────────────────────────────────────────────

SKIP_DATES = {"2026-04-29", "2026-05-06", "2026-05-13"}

def _seed_wednesday_events(cur):
    """Insert the original Wednesday events if the events table is empty."""
    cur.execute("SELECT COUNT(*) as c FROM events")
    if cur.fetchone()["c"] > 0:
        return
    start = datetime(2026, 3, 25)
    end = datetime(2026, 6, 30)
    current = start
    while current.weekday() != 2:
        current += timedelta(days=1)
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        if date_str not in SKIP_DATES:
            cur.execute(
                _p("INSERT INTO events (title, event_date, start_time, end_time, max_capacity) VALUES (%s, %s, %s, %s, %s)"),
                ("Tutoring Session", date_str, "16:00", "17:00", 15),
            )
        current += timedelta(days=7)

# ── DB init ───────────────────────────────────────────────────────────────────

def init_db():
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
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
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                max_capacity INTEGER NOT NULL DEFAULT 15,
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hours (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                work_date TEXT NOT NULL,
                hours REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
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
        _seed_wednesday_events(cur)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = _sqlite_dict_factory
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
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                max_capacity INTEGER NOT NULL DEFAULT 15,
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hours (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                hours REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("SELECT id FROM users WHERE username = ?", ("AndyAlbert",))
        if not cur.fetchone():
            pw_hash = bcrypt.hashpw("BrainSprouts2000".encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
                ("AndyAlbert", pw_hash, "Andy Albert"),
            )
        _seed_wednesday_events(cur)
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

# ── Helper: get all event dates from DB ───────────────────────────────────────

def get_event_dates(cur):
    """Return list of event_date strings from the events table, sorted."""
    cur.execute("SELECT event_date FROM events ORDER BY event_date")
    return [r["event_date"] for r in cur.fetchall()]

def get_event_map(cur):
    """Return dict of event_date -> event row."""
    cur.execute("SELECT * FROM events ORDER BY event_date")
    return {r["event_date"]: r for r in cur.fetchall()}

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
    cur.execute(_p("SELECT * FROM users WHERE username = %s"), (username,))
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

# ── Routes: Admin Users ──────────────────────────────────────────────────────

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
    cur.execute(_p("SELECT id FROM users WHERE username = %s"), (username,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "Username already exists"}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        _p("INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)"),
        (username, pw_hash, display_name),
    )
    get_db().commit()
    cur.close()
    return jsonify({"message": "User created successfully"}), 201

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    cur = get_cursor()
    cur.execute(_p("DELETE FROM rsvps WHERE user_id = %s"), (user_id,))
    if USE_PG:
        cur.execute(_p("DELETE FROM users WHERE id = %s AND is_admin = FALSE"), (user_id,))
    else:
        cur.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "User deleted"})

# ── Routes: Admin Events ─────────────────────────────────────────────────────

@app.route("/api/admin/events", methods=["GET"])
@admin_required
def admin_events():
    cur = get_cursor()
    event_map = get_event_map(cur)
    result = []
    for date, ev in event_map.items():
        cur.execute(_p("""
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
            "id": ev["id"],
            "date": date,
            "title": ev["title"],
            "start_time": ev["start_time"],
            "end_time": ev["end_time"],
            "max_capacity": ev["max_capacity"],
            "yes_count": yes_count,
            "maybe_count": maybe_count,
            "no_count": no_count,
            "at_capacity": yes_count >= ev["max_capacity"],
            "rsvps": [dict(r) for r in rsvps],
        })
    cur.close()
    return jsonify(result)

@app.route("/api/admin/events", methods=["POST"])
@admin_required
def create_event():
    data = request.get_json()
    title = data.get("title", "").strip()
    event_date = data.get("event_date", "").strip()
    start_time = data.get("start_time", "").strip()
    end_time = data.get("end_time", "").strip()
    max_capacity = data.get("max_capacity", 15)
    if not title or not event_date or not start_time or not end_time:
        return jsonify({"error": "All fields required"}), 400
    try:
        datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format (use YYYY-MM-DD)"}), 400

    cur = get_cursor()
    cur.execute(_p("SELECT id FROM events WHERE event_date = %s"), (event_date,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "An event already exists on this date"}), 409

    cur.execute(
        _p("INSERT INTO events (title, event_date, start_time, end_time, max_capacity) VALUES (%s, %s, %s, %s, %s)"),
        (title, event_date, start_time, end_time, int(max_capacity)),
    )
    get_db().commit()
    cur.close()
    return jsonify({"message": "Event created"}), 201

@app.route("/api/admin/events/repeat", methods=["POST"])
@admin_required
def create_repeating_events():
    data = request.get_json()
    title = data.get("title", "").strip()
    start_date = data.get("start_date", "").strip()
    end_date = data.get("end_date", "").strip()
    day_of_week = data.get("day_of_week")  # 0=Mon, 6=Sun
    start_time = data.get("start_time", "").strip()
    end_time = data.get("end_time", "").strip()
    max_capacity = data.get("max_capacity", 15)
    skip_dates = set(data.get("skip_dates", []))

    if not all([title, start_date, end_date, start_time, end_time, day_of_week is not None]):
        return jsonify({"error": "All fields required"}), 400
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        day_of_week = int(day_of_week)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid date or day_of_week"}), 400
    if day_of_week < 0 or day_of_week > 6:
        return jsonify({"error": "day_of_week must be 0 (Mon) to 6 (Sun)"}), 400

    cur = get_cursor()
    current = sd
    # Advance to first matching day
    while current.weekday() != day_of_week:
        current += timedelta(days=1)

    created = 0
    skipped = 0
    while current <= ed:
        date_str = current.strftime("%Y-%m-%d")
        current += timedelta(days=7)
        if date_str in skip_dates:
            skipped += 1
            continue
        cur.execute(_p("SELECT id FROM events WHERE event_date = %s"), (date_str,))
        if cur.fetchone():
            skipped += 1
            continue
        cur.execute(
            _p("INSERT INTO events (title, event_date, start_time, end_time, max_capacity) VALUES (%s, %s, %s, %s, %s)"),
            (title, date_str, start_time, end_time, int(max_capacity)),
        )
        created += 1

    get_db().commit()
    cur.close()
    return jsonify({"message": f"{created} events created, {skipped} skipped (duplicates or excluded)"}), 201

@app.route("/api/admin/events/<int:event_id>", methods=["DELETE"])
@admin_required
def delete_event(event_id):
    cur = get_cursor()
    # Get the event date so we can clean up RSVPs
    cur.execute(_p("SELECT event_date FROM events WHERE id = %s"), (event_id,))
    ev = cur.fetchone()
    if ev:
        cur.execute(_p("DELETE FROM rsvps WHERE event_date = %s"), (ev["event_date"],))
    cur.execute(_p("DELETE FROM events WHERE id = %s"), (event_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Event deleted"})

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
    cur.execute(_p("INSERT INTO announcements (title, body) VALUES (%s, %s)"), (title, body))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Announcement posted"}), 201

@app.route("/api/admin/announcements/<int:ann_id>", methods=["DELETE"])
@admin_required
def delete_announcement(ann_id):
    cur = get_cursor()
    cur.execute(_p("DELETE FROM announcements WHERE id = %s"), (ann_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Announcement deleted"})

# ── Routes: Calendar / RSVP (tutor-facing) ───────────────────────────────────

@app.route("/api/events", methods=["GET"])
@token_required
def get_events():
    cur = get_cursor()
    event_map = get_event_map(cur)
    cur.execute(_p("SELECT event_date, status FROM rsvps WHERE user_id = %s"), (g.user_id,))
    my_rsvps = cur.fetchall()
    rsvp_map = {r["event_date"]: r["status"] for r in my_rsvps}

    result = []
    for date, ev in event_map.items():
        cur.execute(
            _p("SELECT COUNT(*) as c FROM rsvps WHERE event_date = %s AND status = 'yes'"), (date,)
        )
        yes_count = cur.fetchone()["c"]
        result.append({
            "date": date,
            "title": ev["title"],
            "start_time": ev["start_time"],
            "end_time": ev["end_time"],
            "max_capacity": ev["max_capacity"],
            "my_status": rsvp_map.get(date),
            "yes_count": yes_count,
            "at_capacity": yes_count >= ev["max_capacity"],
        })
    cur.close()
    return jsonify(result)

@app.route("/api/rsvp", methods=["POST"])
@token_required
def set_rsvp():
    data = request.get_json()
    event_date = data.get("date", "")
    status = data.get("status", "")

    cur = get_cursor()
    cur.execute(_p("SELECT * FROM events WHERE event_date = %s"), (event_date,))
    ev = cur.fetchone()
    if not ev:
        cur.close()
        return jsonify({"error": "Invalid event date"}), 400
    if status not in ("yes", "maybe", "no"):
        cur.close()
        return jsonify({"error": "Status must be yes, maybe, or no"}), 400

    if status == "yes":
        cur.execute(
            _p("SELECT COUNT(*) as c FROM rsvps WHERE event_date = %s AND status = 'yes' AND user_id != %s"),
            (event_date, g.user_id),
        )
        if cur.fetchone()["c"] >= ev["max_capacity"]:
            cur.close()
            return jsonify({"error": f"Event is at full capacity ({ev['max_capacity']}/{ev['max_capacity']})"}), 409

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

# ── Routes: Hours (tutor-facing) ──────────────────────────────────────────────

@app.route("/api/hours", methods=["GET"])
@token_required
def get_my_hours():
    cur = get_cursor()
    cur.execute(_p("""
        SELECT h.id, h.work_date, h.hours, h.description, h.created_at
        FROM hours h
        WHERE h.user_id = %s
        ORDER BY h.work_date DESC
    """), (g.user_id,))
    rows = cur.fetchall()
    cur.close()
    return jsonify([serialize_row(r) for r in rows])

# ── Routes: Admin Hours ──────────────────────────────────────────────────────

@app.route("/api/admin/hours", methods=["GET"])
@admin_required
def admin_get_hours():
    cur = get_cursor()
    cur.execute("""
        SELECT h.id, h.user_id, h.work_date, h.hours, h.description, h.created_at,
               u.display_name
        FROM hours h JOIN users u ON h.user_id = u.id
        ORDER BY h.work_date DESC, u.display_name
    """)
    rows = cur.fetchall()
    cur.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route("/api/admin/hours", methods=["POST"])
@admin_required
def create_hours():
    data = request.get_json()
    user_id = data.get("user_id")
    work_date = data.get("work_date", "").strip()
    hours = data.get("hours")
    description = data.get("description", "").strip()

    if not user_id or not work_date or hours is None:
        return jsonify({"error": "Tutor, date, and hours are required"}), 400
    try:
        hours = float(hours)
        if hours <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Hours must be a positive number"}), 400
    try:
        datetime.strptime(work_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    cur = get_cursor()
    cur.execute(
        _p("INSERT INTO hours (user_id, work_date, hours, description) VALUES (%s, %s, %s, %s)"),
        (int(user_id), work_date, hours, description),
    )
    get_db().commit()
    cur.close()
    return jsonify({"message": "Hours logged successfully"}), 201

@app.route("/api/admin/hours/<int:hour_id>", methods=["PUT"])
@admin_required
def update_hours(hour_id):
    data = request.get_json()
    work_date = data.get("work_date", "").strip()
    hours = data.get("hours")
    description = data.get("description", "").strip()

    if not work_date or hours is None:
        return jsonify({"error": "Date and hours are required"}), 400
    try:
        hours = float(hours)
        if hours <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Hours must be a positive number"}), 400

    cur = get_cursor()
    cur.execute(
        _p("UPDATE hours SET work_date = %s, hours = %s, description = %s WHERE id = %s"),
        (work_date, hours, description, hour_id),
    )
    get_db().commit()
    cur.close()
    return jsonify({"message": "Hours updated"})

@app.route("/api/admin/hours/<int:hour_id>", methods=["DELETE"])
@admin_required
def delete_hours(hour_id):
    cur = get_cursor()
    cur.execute(_p("DELETE FROM hours WHERE id = %s"), (hour_id,))
    get_db().commit()
    cur.close()
    return jsonify({"message": "Hours deleted"})

# ── Main ──────────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    mode = "PostgreSQL" if USE_PG else "SQLite (local)"
    print(f"BrainSprouts Tutor Management running at http://localhost:5000 [{mode}]")
    app.run(debug=True, port=5000)
