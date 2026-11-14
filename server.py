import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "brainsprouts.db"
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

EVENT_LOCATIONS = {
    "Aurora Public Library": "15145 Yonge Street, Aurora, ON L4G 1M1, Canada",
    "Denne Public School": "330 Burford Street, Newmarket, ON L3Y 6L1, Canada",
    "Pheobe Gilman Public School": (
        "Phoebe Gilman Public School, 145 Harvest Hills Boulevard, "
        "East Gwillimbury, ON L9N 0C1, Canada"
    ),
    "Hartman Public School": (
        "130 River Ridge Boulevard, Aurora, ON L4G 7T7, Canada"
    ),
    "Terry Fox Public School": (
        "161 Sawmill Valley Drive, Newmarket, ON L3X 2T1, Canada"
    ),
}

# Places where tutoring hours can be logged. Kept separate from EVENT_LOCATIONS
# because hours are only tracked for these three sites.
HOUR_LOCATIONS = [
    "Aurora Public Library",
    "Hartman P.S.",
    "Northern Lights P.S.",
    "CICS",
]

HIGH_SCHOOLS = [
    (
        "Denison High School",
        "Dr. J.M. Denison Secondary School, 135 Bristol Road, "
        "Newmarket, ON L3Y 8J7, Canada",
    ),
    (
        "Aurora High School",
        "Aurora High School, 155 Wellington Street West, "
        "Aurora, ON L4G 2P4, Canada",
    ),
    (
        "Newmarket High School",
        "Newmarket High School, 505 Pickering Crescent, "
        "Newmarket, ON L3Y 8H1, Canada",
    ),
    (
        "Dr. G.W. Williams High School",
        "Dr. G.W. Williams Secondary School, 11 Spring Farm Road, "
        "Aurora, ON L4G 7W2, Canada",
    ),
    (
        "Mulock High School",
        "Sir William Mulock Secondary School, 705 Columbus Way, "
        "Newmarket, ON L3X 2M7, Canada",
    ),
]

# Fixed driving estimates, stored as (duration minutes, distance kilometres).
# Rows are event locations and columns are the high schools listed above.
FIXED_ROUTE_MATRIX = {
    "Aurora Public Library": {
        "Denison High School": (14, 8.8),
        "Aurora High School": (3, 1.7),
        "Newmarket High School": (13, 8.4),
        "Dr. G.W. Williams High School": (7, 3.8),
        "Mulock High School": (8, 4.8),
    },
    "Denne Public School": {
        "Denison High School": (5, 2.0),
        "Aurora High School": (17, 10.5),
        "Newmarket High School": (11, 6.0),
        "Dr. G.W. Williams High School": (14, 7.6),
        "Mulock High School": (11, 6.0),
    },
    "Pheobe Gilman Public School": {
        "Denison High School": (5, 2.2),
        "Aurora High School": (15, 11.5),
        "Newmarket High School": (15, 8.9),
        "Dr. G.W. Williams High School": (17, 10.5),
        "Mulock High School": (11, 6.0),
    },
    "Hartman Public School": {
        "Denison High School": (16, 10.3),
        "Aurora High School": (7, 4.2),
        "Newmarket High School": (10, 5.9),
        "Dr. G.W. Williams High School": (2, 0.8),
        "Mulock High School": (11, 6.4),
    },
    "Terry Fox Public School": {
        "Denison High School": (9, 5.5),
        "Aurora High School": (9, 6.9),
        "Newmarket High School": (9, 5.1),
        "Dr. G.W. Williams High School": (10, 6.0),
        "Mulock High School": (2, 0.7),
    },
}

if os.getenv("RENDER") == "true" and not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required on Render. Connect the existing Render "
        "PostgreSQL database instead of using ephemeral SQLite storage."
    )

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


DEFAULT_ROLES = [
    "Jr. Tutor",
    "Reserve Tutor",
    "Tutor",
    "Sr. Tutor",
    "Lead Tutor",
    "Executive Tutor",
    "Marketing Director",
    "Hartman Lead",
    "CICS Lead",
]


def dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def postgres_sql(sql: str) -> str:
    """Translate the small SQLite-compatible query subset used by this app."""
    return sql.replace("datetime('now')", "NOW()").replace("?", "%s")


class Database:
    """Expose one query interface for local SQLite and Render PostgreSQL."""

    def __init__(self):
        if USE_POSTGRES:
            self.connection = psycopg2.connect(DATABASE_URL)
        else:
            # Each request gets its own connection, but FastAPI may open it in one
            # worker thread and run the endpoint in another when requests overlap.
            self.connection = sqlite3.connect(DB_PATH, check_same_thread=False)
            self.connection.row_factory = dict_factory

    def execute(self, sql, params=()):
        if USE_POSTGRES:
            cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(postgres_sql(sql), params)
            return cursor
        return self.connection.execute(sql, params)

    def executemany(self, sql, params):
        if USE_POSTGRES:
            cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.executemany(postgres_sql(sql), params)
            return cursor
        return self.connection.executemany(sql, params)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()


def init_db():
    db = Database()
    try:
        if USE_POSTGRES:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    executive_title TEXT NOT NULL DEFAULT 'Jr. Tutor',
                    is_approved BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    max_capacity INTEGER NOT NULL DEFAULT 15,
                    location_tag TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rsvps (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    event_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('yes','maybe','no')),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, event_date)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS hours (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    work_date TEXT NOT NULL,
                    hours REAL NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    location TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS roles (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    rank INTEGER NOT NULL DEFAULT 0
                )
                """,
            ]
            for statement in statements:
                db.execute(statement)
            db.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS executive_title "
                "TEXT NOT NULL DEFAULT 'Jr. Tutor'"
            )
            # Existing accounts predate self sign-up, so they default to approved.
            db.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_approved "
                "BOOLEAN NOT NULL DEFAULT TRUE"
            )
            db.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS location_tag TEXT"
            )
            db.execute("ALTER TABLE hours ADD COLUMN IF NOT EXISTS location TEXT")
        else:
            db.connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    executive_title TEXT NOT NULL DEFAULT 'Jr. Tutor',
                    is_approved INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    max_capacity INTEGER NOT NULL DEFAULT 15,
                    location_tag TEXT,
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
                    location TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    rank INTEGER NOT NULL DEFAULT 0
                );
            """)
            user_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
            }
            if "executive_title" not in user_columns:
                db.execute(
                    "ALTER TABLE users ADD COLUMN executive_title "
                    "TEXT NOT NULL DEFAULT 'Jr. Tutor'"
                )
            if "is_approved" not in user_columns:
                # Existing accounts predate self sign-up, so they default to approved.
                db.execute(
                    "ALTER TABLE users ADD COLUMN is_approved "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            event_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(events)").fetchall()
            }
            if "location_tag" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN location_tag TEXT")
            hour_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(hours)").fetchall()
            }
            if "location" not in hour_columns:
                db.execute("ALTER TABLE hours ADD COLUMN location TEXT")

        if not db.execute(
            "SELECT id FROM users WHERE username = ?", ("AndyAlbert",)
        ).fetchone():
            password_hash = bcrypt.hashpw(
                b"BrainSprouts2000", bcrypt.gensalt()
            ).decode()
            db.execute(
                "INSERT INTO users "
                "(username, password_hash, display_name, is_admin) "
                "VALUES (?, ?, ?, ?)",
                ("AndyAlbert", password_hash, "Andy Albert", True),
            )

        if db.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"] == 0:
            day = datetime(2026, 3, 25)
            while day <= datetime(2026, 6, 30):
                db.execute(
                    "INSERT INTO events "
                    "(title, event_date, start_time, end_time, max_capacity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "Tutoring Session",
                        day.strftime("%Y-%m-%d"),
                        "16:00",
                        "17:00",
                        15,
                    ),
                )
                day += timedelta(days=7)

        if db.execute("SELECT COUNT(*) AS count FROM roles").fetchone()["count"] == 0:
            db.executemany(
                "INSERT INTO roles (name, rank) VALUES (?, ?)",
                [(name, rank) for rank, name in enumerate(DEFAULT_ROLES)],
            )

        db.commit()
        print(f"Database ready: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def normalize_location_tag(location_tag: str | None) -> str | None:
    normalized = (location_tag or "").strip()
    if not normalized:
        return None
    if normalized not in EVENT_LOCATIONS:
        raise HTTPException(400, "Unknown event location")
    return normalized


def normalize_hour_location(location: str | None, required: bool = False) -> str | None:
    normalized = (location or "").strip()
    if not normalized:
        if required:
            raise HTTPException(400, "Choose a location for these hours")
        return None
    if normalized not in HOUR_LOCATIONS:
        raise HTTPException(400, "Unknown hours location")
    return normalized


def format_drive_time(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining_minutes = divmod(minutes, 60)
    if not remaining_minutes:
        return f"{hours} hr"
    return f"{hours} hr {remaining_minutes} min"


def format_drive_distance(kilometres: float) -> str:
    return f"{kilometres:.1f} km"


def fixed_driving_distances(location_tag: str) -> list[dict]:
    destination_address = EVENT_LOCATIONS[location_tag]
    distances = []
    for school_name, school_address in HIGH_SCHOOLS:
        duration_minutes, distance_kilometres = FIXED_ROUTE_MATRIX[location_tag][
            school_name
        ]
        directions_query = urlencode(
            {
                "api": "1",
                "origin": school_address,
                "destination": destination_address,
                "travelmode": "driving",
            }
        )
        distances.append(
            {
                "school": school_name,
                "duration_seconds": duration_minutes * 60,
                "duration_text": format_drive_time(duration_minutes),
                "distance_meters": round(distance_kilometres * 1000),
                "distance_text": format_drive_distance(distance_kilometres),
                "google_maps_url": (
                    f"https://www.google.com/maps/dir/?{directions_query}"
                ),
            }
        )
    return distances


def event_location_payload(event: dict) -> dict:
    location_tag = event.get("location_tag")
    return {
        "location_tag": location_tag,
        "distances": (
            fixed_driving_distances(location_tag)
            if location_tag in EVENT_LOCATIONS
            else []
        ),
    }


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(LoginBody):
    display_name: str


class SignupBody(LoginBody):
    confirm_password: str


class PasswordVerificationBody(BaseModel):
    current_password: str


class ChangePasswordBody(PasswordVerificationBody):
    new_password: str
    confirm_password: str


class TitleBody(BaseModel):
    executive_title: str


class DisplayNameBody(BaseModel):
    display_name: str


DISPLAY_NAME_MAX_LENGTH = 100


def clean_display_name(display_name: str) -> str:
    # Collapse stray spaces so "  Andy   Yu " is stored as "Andy Yu".
    cleaned = " ".join(display_name.split())
    if not cleaned:
        raise HTTPException(400, "Name cannot be empty")
    if len(cleaned) > DISPLAY_NAME_MAX_LENGTH:
        raise HTTPException(400, f"Name must be {DISPLAY_NAME_MAX_LENGTH} characters or fewer")
    return cleaned


class RoleBody(BaseModel):
    name: str


class RoleOrderBody(BaseModel):
    role_ids: list[int]


class EventBody(BaseModel):
    title: str
    event_date: str | None = None
    start_time: str
    end_time: str
    max_capacity: int = 15
    location_tag: str | None = None


class CreateEventBody(EventBody):
    event_date: str
    repeat_count: int = 1


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
    location: str | None = None


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


Db = Annotated[Database, Depends(get_db)]
User = Annotated[dict, Depends(current_user)]
Admin = Annotated[dict, Depends(current_admin)]


def password_matches(password: str, password_hash: str) -> bool:
    """Safely compare a supplied password with a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (TypeError, ValueError):
        return False


def authenticated_user_password(db: Database, auth: dict) -> dict:
    user = db.execute(
        "SELECT id, password_hash FROM users WHERE id = ?", (auth["user_id"],)
    ).fetchone()
    if not user:
        raise HTTPException(401, "Account not found")
    return user


@app.get("/")
def login_page():
    return FileResponse(BASE_DIR / "templates" / "login.html")


@app.get("/signup")
def signup_page():
    return FileResponse(BASE_DIR / "templates" / "signup.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(BASE_DIR / "templates" / "dashboard.html")


@app.get("/admin")
def admin_page():
    return FileResponse(BASE_DIR / "templates" / "admin.html")


@app.post("/api/login")
def login(data: LoginBody, db: Db):
    user = db.execute("SELECT * FROM users WHERE username = ?", (data.username.strip(),)).fetchone()
    if not user or not password_matches(data.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    if not user["is_approved"]:
        raise HTTPException(
            403, "Your account is still waiting for approval from a cofounder."
        )
    token = jwt.encode({"user_id": user["id"], "is_admin": bool(user["is_admin"]),
        "exp": datetime.utcnow() + timedelta(hours=12)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "user": {"id": user["id"], "username": user["username"],
        "display_name": user["display_name"], "is_admin": bool(user["is_admin"]),
        "executive_title": user["executive_title"]}}


@app.post("/api/signup", status_code=201)
def signup(data: SignupBody, db: Db):
    """Create a self-registered account that cannot sign in until an admin approves it."""
    username = data.username.strip()
    if not 3 <= len(username) <= 40:
        raise HTTPException(400, "Username must be between 3 and 40 characters")
    if data.password != data.confirm_password:
        raise HTTPException(400, "Passwords do not match")
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    password_bytes = data.password.encode()
    if len(password_bytes) > 72:
        raise HTTPException(400, "Password must be 72 bytes or fewer")
    # Compared case-insensitively so nobody can request a look-alike of an existing name.
    if db.execute(
        "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone():
        raise HTTPException(409, "That username is already taken")

    password_hash = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode()
    db.execute(
        "INSERT INTO users (username, password_hash, display_name, is_approved) "
        "VALUES (?, ?, ?, ?)",
        (username, password_hash, username, False),
    )
    db.commit()
    return {"message": "Account created. A cofounder needs to approve it before you can sign in."}


@app.post("/api/me/password/verify")
def verify_current_password(data: PasswordVerificationBody, db: Db, auth: User):
    user = authenticated_user_password(db, auth)
    if not password_matches(data.current_password, user["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    return {"verified": True}


@app.put("/api/me/password")
def change_password(data: ChangePasswordBody, db: Db, auth: User):
    user = authenticated_user_password(db, auth)
    if not password_matches(data.current_password, user["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    if data.new_password != data.confirm_password:
        raise HTTPException(400, "New passwords do not match")
    if len(data.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    new_password_bytes = data.new_password.encode()
    if len(new_password_bytes) > 72:
        raise HTTPException(400, "New password must be 72 bytes or fewer")

    password_hash = bcrypt.hashpw(new_password_bytes, bcrypt.gensalt()).decode()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (password_hash, user["id"]),
    )
    db.commit()
    return {"message": "Password changed successfully"}


@app.get("/api/admin/users")
def list_users(db: Db, _admin: Admin):
    return db.execute(
        "SELECT id, username, display_name, is_admin, executive_title, created_at "
        "FROM users WHERE is_approved = ? ORDER BY display_name",
        (True,),
    ).fetchall()


@app.get("/api/admin/pending-users")
def list_pending_users(db: Db, _admin: Admin):
    return db.execute(
        "SELECT id, username, created_at FROM users "
        "WHERE is_approved = ? ORDER BY created_at, id",
        (False,),
    ).fetchall()


def pending_user(db: Database, user_id: int) -> dict:
    user = db.execute(
        "SELECT id, username FROM users WHERE id = ? AND is_approved = ?",
        (user_id, False),
    ).fetchone()
    if not user:
        raise HTTPException(404, "This sign-up request no longer exists")
    return user


@app.post("/api/admin/pending-users/{user_id}/approve")
def approve_pending_user(user_id: int, db: Db, _admin: Admin):
    user = pending_user(db, user_id)
    db.execute("UPDATE users SET is_approved = ? WHERE id = ?", (True, user["id"]))
    db.commit()
    return {"message": f"{user['username']} was approved"}


@app.delete("/api/admin/pending-users/{user_id}")
def deny_pending_user(user_id: int, db: Db, _admin: Admin):
    # Denying removes the request entirely, which also frees up the username.
    user = pending_user(db, user_id)
    db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    db.commit()
    return {"message": f"{user['username']} was denied"}


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
    title = data.executive_title.strip()
    if not db.execute("SELECT id FROM roles WHERE name = ?", (title,)).fetchone():
        raise HTTPException(400, "Unknown role")
    db.execute(
        "UPDATE users SET executive_title = ? WHERE id = ?", (title, user_id)
    )
    db.commit()


@app.put("/api/admin/users/{user_id}/display-name")
def update_display_name(user_id: int, data: DisplayNameBody, db: Db, _admin: Admin):
    display_name = clean_display_name(data.display_name)
    if not db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone():
        raise HTTPException(404, "Tutor not found")
    db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
    db.commit()
    return {"display_name": display_name}


@app.get("/api/roles")
def list_roles(db: Db, _user: User):
    return db.execute(
        "SELECT id, name, rank FROM roles ORDER BY rank, id"
    ).fetchall()


@app.get("/api/event-locations")
def event_locations(_user: User):
    return list(EVENT_LOCATIONS)


@app.get("/api/hour-locations")
def hour_locations(_user: User):
    return HOUR_LOCATIONS


@app.post("/api/admin/roles", status_code=201)
def create_role(data: RoleBody, db: Db, _admin: Admin):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Role name is required")
    next_rank = db.execute(
        "SELECT COALESCE(MAX(rank), -1) + 1 AS next_rank FROM roles"
    ).fetchone()["next_rank"]
    try:
        db.execute(
            "INSERT INTO roles (name, rank) VALUES (?, ?)", (name, next_rank)
        )
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(409, "Role already exists") from error


@app.delete("/api/admin/roles/{role_id}")
def delete_role(role_id: int, db: Db, _admin: Admin):
    role = db.execute(
        "SELECT id, name FROM roles WHERE id = ?", (role_id,)
    ).fetchone()
    if not role:
        raise HTTPException(404, "Role not found")
    if role["name"] == DEFAULT_ROLES[0]:
        raise HTTPException(400, "The default Jr. Tutor role cannot be deleted")
    db.execute(
        "UPDATE users SET executive_title = ? WHERE executive_title = ?",
        (DEFAULT_ROLES[0], role["name"]),
    )
    db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
    db.commit()


@app.put("/api/admin/roles/reorder")
def reorder_roles(data: RoleOrderBody, db: Db, _admin: Admin):
    existing_ids = {
        row["id"] for row in db.execute("SELECT id FROM roles").fetchall()
    }
    requested_ids = data.role_ids
    if (
        not requested_ids
        or len(requested_ids) != len(set(requested_ids))
        or set(requested_ids) != existing_ids
    ):
        raise HTTPException(400, "role_ids must contain every role exactly once")
    db.executemany(
        "UPDATE roles SET rank = ? WHERE id = ?",
        [(rank, role_id) for rank, role_id in enumerate(requested_ids)],
    )
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
            "at_capacity": counts["yes"] >= event["max_capacity"], "rsvps": rsvps,
            **event_location_payload(event)})
    return result


def validated_event_values(data: EventBody) -> tuple[str, str, str]:
    title = data.title.strip()
    start_time = data.start_time.strip()
    end_time = data.end_time.strip()
    if not title:
        raise HTTPException(400, "Event title is required")
    try:
        parsed_start = datetime.strptime(start_time, "%H:%M")
        parsed_end = datetime.strptime(end_time, "%H:%M")
    except ValueError:
        raise HTTPException(400, "Event times must use HH:MM format")
    if parsed_end <= parsed_start:
        raise HTTPException(400, "End time must be after start time")
    if data.max_capacity < 1:
        raise HTTPException(400, "Max capacity must be at least 1")
    return title, start_time, end_time


def validated_event_date(event_date: str) -> str:
    try:
        parsed_date = datetime.strptime(event_date.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Event date must use YYYY-MM-DD format")
    return parsed_date.strftime("%Y-%m-%d")


def weekly_event_dates(event_date: str, repeat_count: int) -> list[str]:
    if repeat_count < 1 or repeat_count > 52:
        raise HTTPException(400, "Repeat count must be between 1 and 52")
    first_date = datetime.strptime(validated_event_date(event_date), "%Y-%m-%d")
    return [
        (first_date + timedelta(weeks=week)).strftime("%Y-%m-%d")
        for week in range(repeat_count)
    ]


@app.post("/api/admin/events", status_code=201)
def create_event(data: CreateEventBody, db: Db, _admin: Admin):
    title, start_time, end_time = validated_event_values(data)
    dates = weekly_event_dates(data.event_date, data.repeat_count)
    conflicting_dates = [
        event_date
        for event_date in dates
        if db.execute(
            "SELECT id FROM events WHERE event_date = ?", (event_date,)
        ).fetchone()
    ]
    if conflicting_dates:
        formatted_dates = ", ".join(conflicting_dates)
        noun = "date" if len(conflicting_dates) == 1 else "dates"
        raise HTTPException(
            409,
            f"Events already exist on the following {noun}: {formatted_dates}",
        )
    location_tag = normalize_location_tag(data.location_tag)
    db.executemany(
        "INSERT INTO events (title, event_date, start_time, end_time, "
        "max_capacity, location_tag) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                title,
                event_date,
                start_time,
                end_time,
                data.max_capacity,
                location_tag,
            )
            for event_date in dates
        ],
    )
    db.commit()
    return {"created_count": len(dates), "dates": dates}


@app.put("/api/admin/events/{event_id}")
def update_event(event_id: int, data: EventBody, db: Db, _admin: Admin):
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event:
        raise HTTPException(404, "Event not found")
    title, start_time, end_time = validated_event_values(data)
    event_date = (
        validated_event_date(data.event_date)
        if data.event_date is not None
        else event["event_date"]
    )
    conflicting_event = db.execute(
        "SELECT id FROM events WHERE event_date = ? AND id != ?",
        (event_date, event_id),
    ).fetchone()
    if conflicting_event:
        raise HTTPException(409, f"An event already exists on {event_date}")
    location_tag = normalize_location_tag(data.location_tag)
    try:
        db.execute(
            "UPDATE events SET title = ?, event_date = ?, start_time = ?, "
            "end_time = ?, max_capacity = ?, location_tag = ? WHERE id = ?",
            (
                title,
                event_date,
                start_time,
                end_time,
                data.max_capacity,
                location_tag,
                event_id,
            ),
        )
        if event_date != event["event_date"]:
            db.execute(
                "UPDATE rsvps SET event_date = ? WHERE event_date = ?",
                (event_date, event["event_date"]),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise


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
            "at_capacity": yes_count >= event["max_capacity"],
            **event_location_payload(event)})
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
    return db.execute("SELECT id, work_date, hours, description, location, created_at FROM hours WHERE user_id = ? ORDER BY work_date DESC",
                      (user["user_id"],)).fetchall()


@app.get("/api/admin/hours")
def all_hours(db: Db, _admin: Admin):
    return db.execute("""SELECT h.id, h.user_id, h.work_date, h.hours, h.description, h.location, h.created_at, u.display_name
        FROM hours h JOIN users u ON h.user_id = u.id ORDER BY h.work_date DESC, u.display_name""").fetchall()


@app.post("/api/admin/hours")
def create_hours(data: LogHoursBody, db: Db, _admin: Admin):
    location = normalize_hour_location(data.location, required=True)
    db.executemany("INSERT INTO hours (user_id, work_date, hours, description, location) VALUES (?, ?, ?, ?, ?)",
        [(user_id, data.work_date, data.hours, data.description.strip(), location) for user_id in data.user_ids])
    db.commit()


@app.put("/api/admin/hours/{hour_id}")
def update_hours(hour_id: int, data: HoursBody, db: Db, _admin: Admin):
    # Entries logged before locations existed may stay unassigned when edited.
    location = normalize_hour_location(data.location)
    db.execute("UPDATE hours SET work_date = ?, hours = ?, description = ?, location = ? WHERE id = ?",
               (data.work_date, data.hours, data.description.strip(), location, hour_id))
    db.commit()


@app.delete("/api/admin/hours/{hour_id}")
def delete_hours(hour_id: int, db: Db, _admin: Admin):
    db.execute("DELETE FROM hours WHERE id = ?", (hour_id,))
    db.commit()


init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
