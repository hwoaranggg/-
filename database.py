import sqlite3
import os
from datetime import date
from zoneinfo import ZoneInfo

DB_PATH  = os.environ.get("DB_PATH", "mentor.db")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Amsterdam")
TZ = ZoneInfo(TIMEZONE)


class Database:
    def __init__(self):
        self.path = DB_PATH
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    name    TEXT,
                    work    TEXT,
                    about   TEXT,
                    created TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS user_meta (
                    user_id INTEGER,
                    key     TEXT,
                    value   TEXT,
                    PRIMARY KEY (user_id, key)
                );

                CREATE TABLE IF NOT EXISTS goals (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER,
                    title       TEXT,
                    priority    TEXT DEFAULT 'средний',
                    description TEXT,
                    created     TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS habits (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    time    TEXT,
                    name    TEXT
                );

                CREATE TABLE IF NOT EXISTS habit_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER,
                    habit     TEXT,
                    done_date TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role    TEXT,
                    content TEXT,
                    created TEXT DEFAULT (datetime('now'))
                );
            """)

    # ── USERS ──────────────────────────────────────────────────────────────────
    def ensure_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

    def get_profile(self, user_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else {}

    def update_profile(self, user_id: int, field: str, value: str):
        if field not in {"name", "work", "about"}:
            return
        with self._conn() as conn:
            conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))

    # ── META (setup_step и прочее) ─────────────────────────────────────────────
    def get_meta(self, user_id: int, key: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM user_meta WHERE user_id=? AND key=?", (user_id, key)
            ).fetchone()
            return row["value"] if row else ""

    def set_meta(self, user_id: int, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO user_meta (user_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                (user_id, key, value)
            )

    # ── GOALS ──────────────────────────────────────────────────────────────────
    def get_goals(self, user_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE user_id=? ORDER BY "
                "CASE priority WHEN 'высокий' THEN 1 WHEN 'средний' THEN 2 ELSE 3 END",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_goal(self, user_id: int, title: str, priority: str, description: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO goals (user_id, title, priority, description) VALUES (?,?,?,?)",
                (user_id, title, priority, description)
            )

    def remove_goal(self, user_id: int, title: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM goals WHERE user_id=? AND title LIKE ?",
                (user_id, f"%{title}%")
            )
            return cur.rowcount > 0

    # ── HABITS ─────────────────────────────────────────────────────────────────
    def get_habits(self, user_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM habits WHERE user_id=? ORDER BY time", (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_habit(self, user_id: int, habit_time: str, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO habits (user_id, time, name) VALUES (?,?,?)",
                (user_id, habit_time, name)
            )

    def mark_habit_done(self, user_id: int, habit_name: str):
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM habit_log WHERE user_id=? AND habit LIKE ? AND done_date=?",
                (user_id, f"%{habit_name}%", today)
            )
            conn.execute(
                "INSERT INTO habit_log (user_id, habit, done_date) VALUES (?,?,?)",
                (user_id, habit_name, today)
            )

    def is_habit_done_today(self, user_id: int, habit_name: str) -> bool:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM habit_log WHERE user_id=? AND habit=? AND done_date=?",
                (user_id, habit_name, today)
            ).fetchone()
            return row is not None

    # ── MESSAGES ───────────────────────────────────────────────────────────────
    def save_message(self, user_id: int, role: str, content: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?,?,?)",
                (user_id, role, content)
            )
            conn.execute("""
                DELETE FROM messages WHERE id IN (
                    SELECT id FROM messages WHERE user_id=?
                    ORDER BY id DESC LIMIT -1 OFFSET 200
                )
            """, (user_id,))

    def get_recent_messages(self, user_id: int, limit: int = 30) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
