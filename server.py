import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "brainsprouts.db"
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))


def dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = dict_factory
    try:
        yield db
    finally:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = dict_factory
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            executive_title TEXT NOT NULL DEFAULT 'Jr. Tutor',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            max_capacity INTEGER NOT NULL DEFAULT 15,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS rsvps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            event_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('yes','maybe','no')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, event_date)
        );
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            work_date TEXT NOT NULL,
            hours REAL NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    if not db.execute("SELECT id FROM users WHERE username = ?", ("AndyAlbert",)).fetchone():
        password_hash = bcrypt.hashpw(b"BrainSprouts2000", bcrypt.gensalt()).decode()
        db.execute("INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
                   ("AndyAlbert", password_hash, "Andy Albert"))
    if db.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"] == 0:
        day = datetime(2026, 3, 25)
        while day <= datetime(2026, 6, 30):
            db.execute("INSERT INTO events (title, event_date, start_time, end_time, max_capacity) VALUES (?, ?, ?, ?, ?)",
                       ("Tutoring Session", day.strftime("%Y-%m-%d"), "16:00", "17:00", 15))
            day += timedelta(days=7)
    db.commit()
    db.close()


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(LoginBody):
    display_name: str


class TitleBody(BaseModel):
    executive_title: str


class EventBody(BaseModel):
    title: str
    start_time: str
    end_time: str
    max_capacity: int = 15


class CreateEventBody(EventBody):
    event_date: str


class AnnouncementBody(BaseModel):
    title: str
    body: str


class RsvpBody(BaseModel):
    date: str
    status: str


class HoursBody(BaseModel):
    work_date: str
    hours: float
    description: str = ""


class LogHoursBody(HoursBody):
    user_ids: list[int]


app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"))


def current_user(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token required")
    try:
        return jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")


def current_admin(user: Annotated[dict, Depends(current_user)]):
    if not user["is_admin"]:
        raise HTTPException(403, "Admin access required")
    return user


Db = Annotated[sqlite3.Connection, Depends(get_db)]
User = Annotated[dict, Depends(current_user)]
Admin = Annotated[dict, Depends(current_admin)]


@app.get("/")
def login_page():
    return FileResponse(BASE_DIR / "templates" / "login.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(BASE_DIR / "templates" / "dashboard.html")


@app.get("/admin")
def admin_page():
    return FileResponse(BASE_DIR / "templates" / "admin.html")


@app.post("/api/login")
def login(data: LoginBody, db: Db):
    user = db.execute("SELECT * FROM users WHERE username = ?", (data.username.strip(),)).fetchone()
    if not user or not bcrypt.checkpw(data.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Invalid credentials")
    token = jwt.encode({"user_id": user["id"], "is_admin": bool(user["is_admin"]),
        "exp": datetime.utcnow() + timedelta(hours=12)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "user": {"id": user["id"], "username": user["username"],
        "display_name": user["display_name"], "is_admin": bool(user["is_admin"]),
        "executive_title": user["executive_title"]}}


@app.get("/api/admin/users")
def list_users(db: Db, _admin: Admin):
    return db.execute("SELECT id, username, display_name, is_admin, executive_title, created_at FROM users ORDER BY display_name").fetchall()


@app.post("/api/admin/users")
def create_user(data: UserBody, db: Db, _admin: Admin):
    username, display_name = data.username.strip(), data.display_name.strip()
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        raise HTTPException(409, "Username already exists")
    password_hash = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()
    db.execute("INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
               (username, password_hash, display_name))
    db.commit()


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, db: Db, _admin: Admin):
    db.execute("DELETE FROM rsvps WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM hours WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()


@app.put("/api/admin/users/{user_id}/title")
def update_title(user_id: int, data: TitleBody, db: Db, _admin: Admin):
    db.execute("UPDATE users SET executive_title = ? WHERE id = ?", (data.executive_title.strip(), user_id))
    db.commit()


@app.get("/api/admin/events")
def admin_events(db: Db, _admin: Admin):
    result = []
    for event in db.execute("SELECT * FROM events ORDER BY event_date").fetchall():
        rsvps = db.execute("""SELECT u.display_name, u.username, r.status FROM rsvps r
            JOIN users u ON r.user_id = u.id WHERE r.event_date = ? ORDER BY r.status, u.display_name""",
            (event["event_date"],)).fetchall()
        counts = {choice: sum(rsvp["status"] == choice for rsvp in rsvps) for choice in ("yes", "maybe", "no")}
        result.append({"id": event["id"], "date": event["event_date"], "title": event["title"],
            "start_time": event["start_time"], "end_time": event["end_time"], "max_capacity": event["max_capacity"],
            "yes_count": counts["yes"], "maybe_count": counts["maybe"], "no_count": counts["no"],
            "at_capacity": counts["yes"] >= event["max_capacity"], "rsvps": rsvps})
    return result


@app.post("/api/admin/events")
def create_event(data: CreateEventBody, db: Db, _admin: Admin):
    values = (data.title.strip(), data.event_date.strip(), data.start_time.strip(), data.end_time.strip())
    if db.execute("SELECT id FROM events WHERE event_date = ?", (values[1],)).fetchone():
        raise HTTPException(409, "An event already exists on this date")
    db.execute("INSERT INTO events (title, event_date, start_time, end_time, max_capacity) VALUES (?, ?, ?, ?, ?)",
               (*values, data.max_capacity))
    db.commit()


@app.put("/api/admin/events/{event_id}")
def update_event(event_id: int, data: EventBody, db: Db, _admin: Admin):
    values = (data.title.strip(), data.start_time.strip(), data.end_time.strip())
    db.execute("UPDATE events SET title = ?, start_time = ?, end_time = ?, max_capacity = ? WHERE id = ?",
               (*values, data.max_capacity, event_id))
    db.commit()


@app.delete("/api/admin/events/{event_id}")
def delete_event(event_id: int, db: Db, _admin: Admin):
    event = db.execute("SELECT event_date FROM events WHERE id = ?", (event_id,)).fetchone()
    db.execute("DELETE FROM rsvps WHERE event_date = ?", (event["event_date"],))
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()


@app.get("/api/announcements")
def announcements(db: Db, _user: User):
    return db.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()


@app.post("/api/admin/announcements")
def create_announcement(data: AnnouncementBody, db: Db, _admin: Admin):
    db.execute("INSERT INTO announcements (title, body) VALUES (?, ?)", (data.title.strip(), data.body.strip()))
    db.commit()


@app.delete("/api/admin/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, db: Db, _admin: Admin):
    db.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    db.commit()


@app.get("/api/events")
def events(db: Db, user: User):
    my_rsvps = {row["event_date"]: row["status"] for row in
        db.execute("SELECT event_date, status FROM rsvps WHERE user_id = ?", (user["user_id"],)).fetchall()}
    result = []
    for event in db.execute("SELECT * FROM events ORDER BY event_date").fetchall():
        yes_count = db.execute("SELECT COUNT(*) AS count FROM rsvps WHERE event_date = ? AND status = 'yes'",
                               (event["event_date"],)).fetchone()["count"]
        result.append({"date": event["event_date"], "title": event["title"], "start_time": event["start_time"],
            "end_time": event["end_time"], "max_capacity": event["max_capacity"],
            "my_status": my_rsvps.get(event["event_date"]), "yes_count": yes_count,
            "at_capacity": yes_count >= event["max_capacity"]})
    return result


@app.post("/api/rsvp")
def rsvp(data: RsvpBody, db: Db, user: User):
    event = db.execute("SELECT * FROM events WHERE event_date = ?", (data.date,)).fetchone()
    yes_count = db.execute("SELECT COUNT(*) AS count FROM rsvps WHERE event_date = ? AND status = 'yes' AND user_id != ?",
                           (data.date, user["user_id"])).fetchone()["count"]
    if data.status == "yes" and yes_count >= event["max_capacity"]:
        raise HTTPException(409, f"Event is at full capacity ({event['max_capacity']}/{event['max_capacity']})")
    db.execute("""INSERT INTO rsvps (user_id, event_date, status, updated_at) VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, event_date) DO UPDATE SET status = ?, updated_at = datetime('now')""",
        (user["user_id"], data.date, data.status, data.status))
    db.commit()


@app.get("/api/me")
def me(db: Db, auth: User):
    user = db.execute("SELECT id, display_name, executive_title FROM users WHERE id = ?", (auth["user_id"],)).fetchone()
    total = db.execute("SELECT COALESCE(SUM(hours), 0) AS total FROM hours WHERE user_id = ?",
                       (auth["user_id"],)).fetchone()["total"]
    return {**user, "total_hours": float(total)}


@app.get("/api/hours")
def my_hours(db: Db, user: User):
    return db.execute("SELECT id, work_date, hours, description, created_at FROM hours WHERE user_id = ? ORDER BY work_date DESC",
                      (user["user_id"],)).fetchall()


@app.get("/api/admin/hours")
def all_hours(db: Db, _admin: Admin):
    return db.execute("""SELECT h.id, h.user_id, h.work_date, h.hours, h.description, h.created_at, u.display_name
        FROM hours h JOIN users u ON h.user_id = u.id ORDER BY h.work_date DESC, u.display_name""").fetchall()


@app.post("/api/admin/hours")
def create_hours(data: LogHoursBody, db: Db, _admin: Admin):
    db.executemany("INSERT INTO hours (user_id, work_date, hours, description) VALUES (?, ?, ?, ?)",
        [(user_id, data.work_date, data.hours, data.description.strip()) for user_id in data.user_ids])
    db.commit()


@app.put("/api/admin/hours/{hour_id}")
def update_hours(hour_id: int, data: HoursBody, db: Db, _admin: Admin):
    db.execute("UPDATE hours SET work_date = ?, hours = ?, description = ? WHERE id = ?",
               (data.work_date, data.hours, data.description.strip(), hour_id))
    db.commit()


@app.delete("/api/admin/hours/{hour_id}")
def delete_hours(hour_id: int, db: Db, _admin: Admin):
    db.execute("DELETE FROM hours WHERE id = ?", (hour_id,))
    db.commit()


init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
