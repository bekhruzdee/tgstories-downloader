import asyncpg
import json
from datetime import date, timedelta

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
    return _pool


async def init_db() -> None:
    pool = await _get_pool()
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT UNIQUE,
            username    TEXT,
            full_name   TEXT,
            lang        TEXT DEFAULT 'uz',
            joined_at   TIMESTAMP DEFAULT NOW(),
            last_active TIMESTAMP DEFAULT NOW()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id            SERIAL PRIMARY KEY,
            user_id       BIGINT,
            target        TEXT,
            type          TEXT,
            downloaded_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id               SERIAL PRIMARY KEY,
            user_id          BIGINT,
            target_username  TEXT,
            last_story_ids   TEXT DEFAULT '[]',
            added_at         TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, target_username)
        )
    """)


async def save_user(user_id: int, username: str | None, full_name: str) -> None:
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO users (user_id, username, full_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE SET
            username    = EXCLUDED.username,
            full_name   = EXCLUDED.full_name,
            last_active = NOW()
    """, user_id, username, full_name)


async def log_download(user_id: int, target: str, type: str) -> None:
    pool = await _get_pool()
    await pool.execute(
        "INSERT INTO downloads (user_id, target, type) VALUES ($1, $2, $3)",
        user_id, target, type,
    )


async def get_stats() -> dict:
    pool = await _get_pool()
    today = date.today()
    return {
        "total_users":      await pool.fetchval("SELECT COUNT(*) FROM users"),
        "today_users":      await pool.fetchval("SELECT COUNT(*) FROM users WHERE joined_at::date = $1", today),
        "total_downloads":  await pool.fetchval("SELECT COUNT(*) FROM downloads"),
        "today_downloads":  await pool.fetchval("SELECT COUNT(*) FROM downloads WHERE downloaded_at::date = $1", today),
    }


async def get_all_user_ids() -> list[int]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT user_id FROM users")
    return [r[0] for r in rows]


async def get_recent_users(limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT user_id, username, full_name, joined_at "
        "FROM users ORDER BY joined_at DESC LIMIT $1",
        limit,
    )
    return [
        {"user_id": r[0], "username": r[1], "full_name": r[2], "joined_at": r[3]}
        for r in rows
    ]


async def add_subscription(user_id: int, target_username: str) -> None:
    pool = await _get_pool()
    await pool.execute(
        "INSERT INTO subscriptions (user_id, target_username) VALUES ($1, $2) "
        "ON CONFLICT (user_id, target_username) DO NOTHING",
        user_id, target_username.lower(),
    )


async def get_subscriptions(user_id: int) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT target_username, last_story_ids FROM subscriptions WHERE user_id = $1",
        user_id,
    )
    return [
        {"username": r[0], "last_story_ids": json.loads(r[1] or "[]")}
        for r in rows
    ]


async def remove_subscription(user_id: int, target_username: str) -> None:
    pool = await _get_pool()
    await pool.execute(
        "DELETE FROM subscriptions WHERE user_id = $1 AND target_username = $2",
        user_id, target_username.lower(),
    )


async def get_all_subscriptions() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT user_id, target_username, last_story_ids FROM subscriptions"
    )
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "last_story_ids": json.loads(r[2] or "[]"),
        }
        for r in rows
    ]


async def update_subscription_ids(
    user_id: int, target_username: str, story_ids: list[int]
) -> None:
    pool = await _get_pool()
    await pool.execute(
        "UPDATE subscriptions SET last_story_ids = $1 "
        "WHERE user_id = $2 AND target_username = $3",
        json.dumps(story_ids), user_id, target_username.lower(),
    )


async def get_stats_extended() -> dict:
    pool = await _get_pool()
    today     = date.today()
    week_ago  = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    return {
        "total_users":         await pool.fetchval("SELECT COUNT(*) FROM users"),
        "today_users":         await pool.fetchval("SELECT COUNT(*) FROM users WHERE joined_at::date = $1", today),
        "week_users":          await pool.fetchval("SELECT COUNT(*) FROM users WHERE joined_at::date >= $1", week_ago),
        "month_users":         await pool.fetchval("SELECT COUNT(*) FROM users WHERE joined_at::date >= $1", month_ago),
        "total_downloads":     await pool.fetchval("SELECT COUNT(*) FROM downloads"),
        "today_downloads":     await pool.fetchval("SELECT COUNT(*) FROM downloads WHERE downloaded_at::date = $1", today),
        "username_downloads":  await pool.fetchval("SELECT COUNT(*) FROM downloads WHERE type = 'username'"),
        "url_downloads":       await pool.fetchval("SELECT COUNT(*) FROM downloads WHERE type = 'url'"),
        "total_subscriptions": await pool.fetchval("SELECT COUNT(*) FROM subscriptions"),
    }


async def get_users_page(page: int, per_page: int = 10) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT u.user_id, u.username, u.full_name, u.lang, u.joined_at, "
        "COUNT(d.id) AS download_count "
        "FROM users u LEFT JOIN downloads d ON u.user_id = d.user_id "
        "GROUP BY u.user_id, u.username, u.full_name, u.lang, u.joined_at "
        "ORDER BY u.joined_at DESC "
        "LIMIT $1 OFFSET $2",
        per_page, page * per_page,
    )
    result = [
        {
            "user_id": r[0], "username": r[1], "full_name": r[2],
            "lang": r[3], "joined_at": r[4], "download_count": r[5],
        }
        for r in rows
    ]
    print(f"[get_users_page] page={page}, per_page={per_page}, rows={len(result)}, data={result}")
    return result


async def get_users_count() -> int:
    pool = await _get_pool()
    return await pool.fetchval("SELECT COUNT(*) FROM users")


async def get_recent_downloads(page: int, per_page: int = 20) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT d.user_id, d.target, d.type, d.downloaded_at, "
        "u.full_name, u.username AS uname "
        "FROM downloads d LEFT JOIN users u ON d.user_id = u.user_id "
        "ORDER BY d.downloaded_at DESC "
        "LIMIT $1 OFFSET $2",
        per_page, page * per_page,
    )
    return [
        {
            "user_id": r[0], "target": r[1], "type": r[2],
            "downloaded_at": r[3], "full_name": r[4], "uname": r[5],
        }
        for r in rows
    ]


async def get_downloads_count() -> int:
    pool = await _get_pool()
    return await pool.fetchval("SELECT COUNT(*) FROM downloads")


async def get_all_subscriptions_detailed() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT s.user_id, s.target_username, s.added_at, "
        "u.full_name, u.username AS uname "
        "FROM subscriptions s LEFT JOIN users u ON s.user_id = u.user_id "
        "ORDER BY s.added_at DESC"
    )
    return [
        {
            "user_id": r[0], "target_username": r[1], "added_at": r[2],
            "full_name": r[3], "uname": r[4],
        }
        for r in rows
    ]
