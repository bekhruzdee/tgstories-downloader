import aiosqlite
import json
import os
from datetime import date

DB_PATH = "data/users.db"


async def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER UNIQUE,
                username   TEXT,
                full_name  TEXT,
                lang       TEXT DEFAULT 'uz',
                joined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                target         TEXT,
                type           TEXT,
                downloaded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER,
                target_username  TEXT,
                last_story_ids   TEXT DEFAULT '[]',
                added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, target_username)
            )
        """)
        await db.commit()


async def save_user(user_id: int, username: str | None, full_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username    = excluded.username,
                full_name   = excluded.full_name,
                last_active = CURRENT_TIMESTAMP
        """, (user_id, username, full_name))
        await db.commit()


async def update_lang(user_id: int, lang: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id)
        )
        await db.commit()


async def get_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT lang FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else "uz"


async def log_download(user_id: int, target: str, type: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO downloads (user_id, target, type) VALUES (?, ?, ?)",
            (user_id, target, type),
        )
        await db.commit()


async def get_stats() -> dict:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(joined_at) = ?", (today,)
        ) as c:
            today_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM downloads") as c:
            total_downloads = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM downloads WHERE DATE(downloaded_at) = ?", (today,)
        ) as c:
            today_downloads = (await c.fetchone())[0]
    return {
        "total_users": total_users,
        "today_users": today_users,
        "total_downloads": total_downloads,
        "today_downloads": today_downloads,
    }


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [row[0] for row in await cur.fetchall()]


async def get_recent_users(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, full_name, joined_at "
            "FROM users ORDER BY joined_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [
                {"user_id": r[0], "username": r[1], "full_name": r[2], "joined_at": r[3]}
                for r in await cur.fetchall()
            ]


async def add_subscription(user_id: int, target_username: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO subscriptions (user_id, target_username) VALUES (?, ?)",
            (user_id, target_username.lower()),
        )
        await db.commit()


async def get_subscriptions(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT target_username, last_story_ids FROM subscriptions WHERE user_id = ?",
            (user_id,),
        ) as cur:
            return [
                {"username": r[0], "last_story_ids": json.loads(r[1] or "[]")}
                for r in await cur.fetchall()
            ]


async def remove_subscription(user_id: int, target_username: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND target_username = ?",
            (user_id, target_username.lower()),
        )
        await db.commit()


async def get_all_subscriptions() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, target_username, last_story_ids FROM subscriptions"
        ) as cur:
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "last_story_ids": json.loads(r[2] or "[]"),
                }
                for r in await cur.fetchall()
            ]


async def update_subscription_ids(
    user_id: int, target_username: str, story_ids: list[int]
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET last_story_ids = ? "
            "WHERE user_id = ? AND target_username = ?",
            (json.dumps(story_ids), user_id, target_username.lower()),
        )
        await db.commit()
