# Bot Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database persistence, multilanguage (uz/ru/en), channel subscription gate, 4-button menu, language selection, story subscriptions with APScheduler notifications, and an admin panel to the existing Telegram Stories bot.

**Architecture:** Six new/modified files wired together; `database.py` is the persistence layer, `lang.py` is pure text lookup, `scheduler.py` drives background notifications, and `bot.py` is rewritten to orchestrate all features through an expanded ConversationHandler. No webhooks; `run_polling()` throughout.

**Tech Stack:** pyrofork 2.3.69, python-telegram-bot 22.8, aiosqlite, APScheduler 3.x, Python 3.13

## Global Constraints

- Python 3.13 — use `tuple[X, Y]` / `list[X]` lowercase generics throughout
- DB file at `data/users.db`; create `data/` with `os.makedirs("data", exist_ok=True)`
- All user-visible text routed through `lang.t(lang, key, **kwargs)` — no hardcoded strings in bot.py
- `allow_reentry=True` on ConversationHandler (already set)
- No MessageHandler in `entry_points` (already fixed)
- APScheduler 3.x import: `from apscheduler.schedulers.asyncio import AsyncIOScheduler`
- Subscription check uses `bot.get_chat_member(CHANNEL_USERNAME, user_id)`; on any exception return `True` (don't block)
- Broadcast: 0.05 s sleep between messages; handle `telegram.error.RetryAfter`
- `stories/` folder created with `os.makedirs` before any download; files removed after send

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `config.py` | Modify | Add `ADMIN_ID`, `CHANNEL_USERNAME` |
| `database.py` | Create | All aiosqlite DB operations |
| `lang.py` | Create | TEXTS dict + `t()` lookup |
| `userbot.py` | Modify | Add `get_story_ids()` for scheduler |
| `scheduler.py` | Create | APScheduler job — checks subscriptions every 30 min |
| `bot.py` | Rewrite | All handlers, ConversationHandler, admin commands |

---

## Task 1: Packages + Config

**Files:**
- Modify: `config.py`

**Interfaces:**
- Produces: `ADMIN_ID: int`, `CHANNEL_USERNAME: str` importable from `config`

- [ ] **Step 1: Install packages**

```
pip install aiosqlite apscheduler
```

- [ ] **Step 2: Append to config.py**

```python
ADMIN_ID = 123456789          # replace with real admin Telegram user_id
CHANNEL_USERNAME = "@yourchannel"  # required subscription channel username
```

- [ ] **Step 3: Verify import**

```
python -c "from config import ADMIN_ID, CHANNEL_USERNAME; print(ADMIN_ID, CHANNEL_USERNAME)"
```

Expected: prints the two values without error.

---

## Task 2: database.py

**Files:**
- Create: `database.py`

**Interfaces:**
- Consumes: nothing (standalone module)
- Produces:
  - `async def init_db() -> None`
  - `async def save_user(user_id: int, username: str | None, full_name: str) -> None`
  - `async def update_lang(user_id: int, lang: str) -> None`
  - `async def get_lang(user_id: int) -> str`
  - `async def log_download(user_id: int, target: str, type: str) -> None`
  - `async def get_stats() -> dict`  keys: `total_users`, `today_users`, `total_downloads`, `today_downloads`
  - `async def get_all_user_ids() -> list[int]`
  - `async def get_recent_users(limit: int = 10) -> list[dict]`  keys: `user_id`, `username`, `full_name`, `joined_at`
  - `async def add_subscription(user_id: int, target_username: str) -> None`
  - `async def get_subscriptions(user_id: int) -> list[dict]`  keys: `username`, `last_story_ids`
  - `async def remove_subscription(user_id: int, target_username: str) -> None`
  - `async def get_all_subscriptions() -> list[dict]`  keys: `user_id`, `username`, `last_story_ids`
  - `async def update_subscription_ids(user_id: int, target_username: str, story_ids: list[int]) -> None`

- [ ] **Step 1: Create database.py**

```python
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
```

- [ ] **Step 2: Verify**

```
python -c "
import asyncio, database
async def run():
    await database.init_db()
    await database.save_user(1, 'test', 'Test User')
    print('lang:', await database.get_lang(1))
    await database.update_lang(1, 'ru')
    print('lang after update:', await database.get_lang(1))
    await database.log_download(1, 'durov', 'username')
    stats = await database.get_stats()
    print('stats:', stats)
asyncio.run(run())
"
```

Expected output — no errors, prints `lang: uz`, `lang after update: ru`, stats dict with counts ≥ 1.

---

## Task 3: lang.py

**Files:**
- Create: `lang.py`

**Interfaces:**
- Produces: `def t(lang: str, key: str, **kwargs) -> str`
- Produces: `TEXTS: dict`  — callers check `TEXTS.keys()` to enumerate supported languages

- [ ] **Step 1: Create lang.py**

```python
TEXTS: dict = {
    "uz": {
        "welcome": "👋 Salom! Telegram Stories yuklab oluvchi bot.",
        "choose_method": "📲 Quyidagi usullardan birini tanlang:",
        "btn_username": "👤 Username orqali",
        "btn_url": "🔗 URL orqali",
        "btn_lang": "🌍 Til: O'zbek",
        "btn_mysubs": "🔔 Obunalarim",
        "enter_username": "👤 Username kiriting:\n\nMisol: @durov yoki durov\n\n/cancel — bekor qilish",
        "enter_url": "🔗 Story URL ni yuboring:\n\nMisol: https://t.me/durov/s/123\n\n/cancel — bekor qilish",
        "downloading": "⏳ Storylar yuklanmoqda...",
        "sending": "📤 {i}/{total} yuborilmoqda...",
        "done_user": "✅ @{username} ning {total} ta storiysi yuborildi!",
        "done_url": "✅ Story muvaffaqiyatli yuborildi!",
        "no_stories": "😕 Hozirda faol storysi yo'q.",
        "cancelled": "❌ Bekor qilindi.",
        "private": "🔒 Bu foydalanuvchi profili yopiq!",
        "not_found": "❌ Bunday username topilmadi!",
        "subscribe_required": "⚠️ Botdan foydalanish uchun kanalga obuna bo'ling:\n{channel}\n\nObuna bo'lgach /start bosing.",
        "btn_subscribed": "✅ Obuna bo'ldim",
        "choose_lang": "🌍 Tilni tanlang:",
        "lang_saved": "✅ Til saqlandi!",
        "mysubs_empty": "📭 Sizda hech qanday obuna yo'q.",
        "mysubs_list": "🔔 Obunalaringiz:\n{list}",
        "sub_added": "✅ @{username} obunaga qo'shildi! Har kuni yangi story chiqsa xabar beraman.",
        "sub_removed": "✅ @{username} obunadan olib tashlandi.",
        "sub_btn_add": "➕ Obuna qo'shish",
        "sub_btn_remove": "➖ Olib tashlash",
        "sub_notify": "🔔 @{username} yangi story qo'ydi!",
        "enter_sub_username": "➕ Obuna qo'shish uchun username kiriting:\n\nMisol: @durov\n\n/cancel — bekor qilish",
        "back": "⬅️ Orqaga",
        "try_again": "Boshqa harakat qiling:",
        "bad_url": "❌ Noto'g'ri URL format!\n\nMisol: https://t.me/durov/s/123",
    },
    "ru": {
        "welcome": "👋 Привет! Бот для скачивания Telegram Stories.",
        "choose_method": "📲 Выберите способ:",
        "btn_username": "👤 По username",
        "btn_url": "🔗 По URL",
        "btn_lang": "🌍 Язык: Русский",
        "btn_mysubs": "🔔 Мои подписки",
        "enter_username": "👤 Введите username:\n\nПример: @durov или durov\n\n/cancel — отмена",
        "enter_url": "🔗 Отправьте URL story:\n\nПример: https://t.me/durov/s/123\n\n/cancel — отмена",
        "downloading": "⏳ Загружаю stories...",
        "sending": "📤 Отправляю {i}/{total}...",
        "done_user": "✅ Отправлено {total} stories от @{username}!",
        "done_url": "✅ Story успешно отправлена!",
        "no_stories": "😕 Нет активных stories.",
        "cancelled": "❌ Отменено.",
        "private": "🔒 Профиль закрыт!",
        "not_found": "❌ Username не найден!",
        "subscribe_required": "⚠️ Подпишитесь на канал:\n{channel}\n\nПосле подписки нажмите /start.",
        "btn_subscribed": "✅ Я подписался",
        "choose_lang": "🌍 Выберите язык:",
        "lang_saved": "✅ Язык сохранён!",
        "mysubs_empty": "📭 У вас нет подписок.",
        "mysubs_list": "🔔 Ваши подписки:\n{list}",
        "sub_added": "✅ @{username} добавлен! Буду уведомлять о новых stories.",
        "sub_removed": "✅ @{username} удалён из подписок.",
        "sub_btn_add": "➕ Добавить подписку",
        "sub_btn_remove": "➖ Удалить",
        "sub_notify": "🔔 @{username} опубликовал новую story!",
        "enter_sub_username": "➕ Введите username для подписки:\n\nПример: @durov\n\n/cancel — отмена",
        "back": "⬅️ Назад",
        "try_again": "Попробуйте снова:",
        "bad_url": "❌ Неверный формат URL!\n\nПример: https://t.me/durov/s/123",
    },
    "en": {
        "welcome": "👋 Hello! Telegram Stories downloader bot.",
        "choose_method": "📲 Choose a method:",
        "btn_username": "👤 By username",
        "btn_url": "🔗 By URL",
        "btn_lang": "🌍 Language: English",
        "btn_mysubs": "🔔 My subscriptions",
        "enter_username": "👤 Enter username:\n\nExample: @durov or durov\n\n/cancel — cancel",
        "enter_url": "🔗 Send story URL:\n\nExample: https://t.me/durov/s/123\n\n/cancel — cancel",
        "downloading": "⏳ Downloading stories...",
        "sending": "📤 Sending {i}/{total}...",
        "done_user": "✅ Sent {total} stories from @{username}!",
        "done_url": "✅ Story sent successfully!",
        "no_stories": "😕 No active stories.",
        "cancelled": "❌ Cancelled.",
        "private": "🔒 This profile is private!",
        "not_found": "❌ Username not found!",
        "subscribe_required": "⚠️ Subscribe to the channel:\n{channel}\n\nThen press /start.",
        "btn_subscribed": "✅ I subscribed",
        "choose_lang": "🌍 Choose language:",
        "lang_saved": "✅ Language saved!",
        "mysubs_empty": "📭 You have no subscriptions.",
        "mysubs_list": "🔔 Your subscriptions:\n{list}",
        "sub_added": "✅ @{username} added! I'll notify you about new stories.",
        "sub_removed": "✅ @{username} removed from subscriptions.",
        "sub_btn_add": "➕ Add subscription",
        "sub_btn_remove": "➖ Remove",
        "sub_notify": "🔔 @{username} posted a new story!",
        "enter_sub_username": "➕ Enter username to subscribe:\n\nExample: @durov\n\n/cancel — cancel",
        "back": "⬅️ Back",
        "try_again": "Try again:",
        "bad_url": "❌ Invalid URL format!\n\nExample: https://t.me/durov/s/123",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text
```

- [ ] **Step 2: Verify**

```
python -c "
from lang import t
print(t('uz', 'btn_username'))
print(t('ru', 'done_user', username='durov', total=3))
print(t('en', 'sending', i=2, total=5))
print(t('uz', 'MISSING_KEY'))
"
```

Expected:
```
👤 Username orqali
✅ Отправлено 3 stories от @durov!
📤 Sending 2/5...
MISSING_KEY
```

---

## Task 4: userbot.py — add get_story_ids()

**Files:**
- Modify: `userbot.py` (append one function)

**Interfaces:**
- Consumes: existing `app` (pyrogram Client), `GetPeerStories`, `StoryItem`, `StoryItemSkipped`
- Produces: `async def get_story_ids(username: str) -> list[int]`  — returns IDs of all active stories (full + skipped), empty list on error

- [ ] **Step 1: Append to userbot.py**

Add after the last existing function:

```python
async def get_story_ids(username: str) -> list[int]:
    """Return IDs of all active stories for a user without downloading media."""
    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
        return [s.id for s in r.stories.stories
                if isinstance(s, (StoryItem, StoryItemSkipped))]
    except Exception as e:
        print(f"[DEBUG] get_story_ids({username}): {type(e).__name__}: {e}")
        return []
```

- [ ] **Step 2: Verify import**

```
python -c "from userbot import get_story_ids; print('OK')"
```

Expected: `OK` (no import errors; client doesn't connect on import).

---

## Task 5: scheduler.py

**Files:**
- Create: `scheduler.py`

**Interfaces:**
- Consumes:
  - `database.get_all_subscriptions() -> list[dict]`  (keys: `user_id`, `username`, `last_story_ids`)
  - `database.update_subscription_ids(user_id, username, ids)`
  - `userbot.get_story_ids(username) -> list[int]`
  - `userbot.get_story_by_id(username, story_id) -> tuple[str, str]`  returns `("photo"|"video", path)`
  - `lang.t(lang, key, **kwargs) -> str`
  - `database.get_lang(user_id) -> str`
- Produces:
  - `def start_scheduler(bot) -> None`  — starts APScheduler; `bot` is `telegram.Bot`
  - `def stop_scheduler() -> None`

- [ ] **Step 1: Create scheduler.py**

```python
import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from lang import t
from userbot import get_story_ids, get_story_by_id

log = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_bot = None


async def _check_subscriptions() -> None:
    log.info("Checking subscriptions...")
    subs = await database.get_all_subscriptions()

    for sub in subs:
        user_id: int = sub["user_id"]
        username: str = sub["username"]
        last_ids: set[int] = set(sub["last_story_ids"])

        try:
            current_ids = await get_story_ids(username)
            if not current_ids:
                continue

            new_ids = [sid for sid in current_ids if sid not in last_ids]

            # Always persist current IDs so the next run has a baseline
            await database.update_subscription_ids(user_id, username, current_ids)

            # Skip notification on first check (last_ids was empty — no baseline yet)
            if not last_ids or not new_ids:
                continue

            lang = await database.get_lang(user_id)

            for story_id in new_ids:
                try:
                    os.makedirs("stories", exist_ok=True)
                    media_type, path = await get_story_by_id(username, story_id)
                    caption = t(lang, "sub_notify", username=username)
                    with open(path, "rb") as f:
                        if media_type == "video":
                            await _bot.send_video(user_id, f, caption=caption)
                        else:
                            await _bot.send_photo(user_id, f, caption=caption)
                    if os.path.exists(path):
                        os.remove(path)
                except Exception as e:
                    log.error("Scheduler: failed to send story %s/%s to %s: %s",
                              username, story_id, user_id, e)

        except Exception as e:
            log.error("Scheduler: error processing sub %s for user %s: %s",
                      username, user_id, e)

        await asyncio.sleep(0.3)   # avoid flood between users


def start_scheduler(bot) -> None:
    global _bot
    _bot = bot
    _scheduler.add_job(_check_subscriptions, "interval", minutes=30, id="sub_check")
    _scheduler.start()
    log.info("Scheduler started (30-min interval)")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
```

- [ ] **Step 2: Verify import**

```
python -c "from scheduler import start_scheduler, stop_scheduler; print('OK')"
```

Expected: `OK`.

---

## Task 6: bot.py — full rewrite

**Files:**
- Rewrite: `bot.py`

**Interfaces:**
- Consumes (all must be imported and used):
  - `config.BOT_TOKEN`, `config.ADMIN_ID`, `config.CHANNEL_USERNAME`
  - `database.*` (init_db, save_user, get_lang, update_lang, log_download, get_stats, get_all_user_ids, get_recent_users, add_subscription, get_subscriptions, remove_subscription)
  - `lang.t`
  - `userbot.app` as `userbot_client`, `userbot.get_user_stories`, `userbot.get_story_by_id`
  - `scheduler.start_scheduler`, `scheduler.stop_scheduler`

**ConversationHandler states:**
```
CHOOSING=0, WAITING_USERNAME=1, WAITING_URL=2, CHOOSING_LANG=3, WAITING_SUB_USERNAME=4
```

**State → handler mapping:**

```
entry_points:       [/start → show_menu]

CHOOSING:           /start → show_menu
                    cb "check_sub"        → check_sub_callback
                    cb "by_username"      → ask_for_username
                    cb "by_url"           → ask_for_url
                    cb "lang_menu"        → choose_lang_menu   → CHOOSING_LANG
                    cb "mysubs"           → show_subs
                    cb "add_sub"          → ask_add_sub        → WAITING_SUB_USERNAME
                    cb "remove_sub_*"     → remove_sub
                    cb "back_menu"        → show_menu

WAITING_USERNAME:   /start → show_menu
                    text   → handle_username  → CHOOSING

WAITING_URL:        /start → show_menu
                    text   → handle_url       → CHOOSING

CHOOSING_LANG:      /start  → show_menu
                    cb "lang_uz|ru|en" → save_lang → CHOOSING
                    cb "back_menu"     → show_menu  → CHOOSING

WAITING_SUB_USERNAME: /start → show_menu
                      text   → handle_sub_username → CHOOSING

fallbacks:          /cancel    → cancel         → CHOOSING
                    /start     → show_menu
                    /stats     → stats_cmd      (admin only)
                    /users     → users_cmd      (admin only)
                    /broadcast → broadcast_cmd  (admin only)
```

- [ ] **Step 1: Write bot.py**

```python
import asyncio
import logging
import os
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import database
from config import ADMIN_ID, BOT_TOKEN, CHANNEL_USERNAME
from lang import t
from scheduler import start_scheduler, stop_scheduler
from userbot import app as userbot_client, get_story_by_id, get_user_stories

log = logging.getLogger(__name__)

CHOOSING, WAITING_USERNAME, WAITING_URL, CHOOSING_LANG, WAITING_SUB_USERNAME = range(5)

# ── helpers ───────────────────────────────────────────────────────────────────

def main_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "btn_username"), callback_data="by_username"),
            InlineKeyboardButton(t(lang, "btn_url"),      callback_data="by_url"),
        ],
        [
            InlineKeyboardButton(t(lang, "btn_lang"),   callback_data="lang_menu"),
            InlineKeyboardButton(t(lang, "btn_mysubs"), callback_data="mysubs"),
        ],
    ])


def parse_story_url(url: str) -> tuple[str | None, int | None]:
    match = re.search(r'(?:https?://)?t\.me/([^/\s]+)/s/(\d+)', url.strip())
    if match:
        return match.group(1), int(match.group(2))
    return None, None


async def is_subscribed(bot, user_id: int) -> bool:
    if not CHANNEL_USERNAME:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return True


async def _send_or_edit(update: Update, text: str, keyboard=None) -> None:
    """Edit existing message if callback_query, otherwise send new."""
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def _subs_view(user_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Build (text, keyboard) for the subscriptions screen."""
    subs = await database.get_subscriptions(user_id)
    buttons: list[list[InlineKeyboardButton]] = []
    if subs:
        listing = "\n".join(f"• @{s['username']}" for s in subs)
        text = t(lang, "mysubs_list", list=listing)
        for sub in subs:
            buttons.append([InlineKeyboardButton(
                f"➖ @{sub['username']}",
                callback_data=f"remove_sub_{sub['username']}",
            )])
    else:
        text = t(lang, "mysubs_empty")
    buttons.append([InlineKeyboardButton(t(lang, "sub_btn_add"), callback_data="add_sub")])
    buttons.append([InlineKeyboardButton(t(lang, "back"), callback_data="back_menu")])
    return text, InlineKeyboardMarkup(buttons)

# ── core menu ─────────────────────────────────────────────────────────────────

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await database.save_user(user.id, user.username, user.full_name)
    lang = await database.get_lang(user.id)

    if update.callback_query:
        await update.callback_query.answer()

    if not await is_subscribed(context.bot, user.id):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "btn_subscribed"), callback_data="check_sub")
        ]])
        await _send_or_edit(update, t(lang, "subscribe_required", channel=CHANNEL_USERNAME), kb)
        return CHOOSING

    await _send_or_edit(update, t(lang, "choose_method"), main_keyboard(lang))
    return CHOOSING


async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.callback_query.from_user.id
    lang = await database.get_lang(user_id)
    if await is_subscribed(context.bot, user_id):
        await update.callback_query.answer()
        return await show_menu(update, context)
    await update.callback_query.answer(t(lang, "subscribe_required", channel=CHANNEL_USERNAME)[:200], show_alert=True)
    return CHOOSING

# ── download by username ──────────────────────────────────────────────────────

async def ask_for_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await database.get_lang(update.callback_query.from_user.id)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(t(lang, "enter_username"))
    return WAITING_USERNAME


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    lang = await database.get_lang(user.id)
    username = update.message.text.strip().lstrip("@")

    msg = await update.message.reply_text(t(lang, "downloading"))

    try:
        files = await get_user_stories(username)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(t(lang, "try_again"), reply_markup=main_keyboard(lang))
        return CHOOSING

    if not files:
        await msg.edit_text(t(lang, "no_stories"))
        await update.message.reply_text(t(lang, "try_again"), reply_markup=main_keyboard(lang))
        return CHOOSING

    await database.log_download(user.id, username, "username")
    total = len(files)

    for i, (media_type, path) in enumerate(files, 1):
        try:
            await msg.edit_text(t(lang, "sending", i=i, total=total))
            caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username} — {i}/{total}"
            with open(path, "rb") as f:
                if media_type == "video":
                    await update.message.reply_video(video=f, caption=caption)
                else:
                    await update.message.reply_photo(photo=f, caption=caption)
        finally:
            if os.path.exists(path):
                os.remove(path)

    await msg.edit_text(
        t(lang, "done_user", username=username, total=total),
        reply_markup=main_keyboard(lang),
    )
    return CHOOSING

# ── download by URL ───────────────────────────────────────────────────────────

async def ask_for_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await database.get_lang(update.callback_query.from_user.id)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(t(lang, "enter_url"))
    return WAITING_URL


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    lang = await database.get_lang(user.id)
    username, story_id = parse_story_url(update.message.text)

    if not username or not story_id:
        await update.message.reply_text(t(lang, "bad_url"))
        return WAITING_URL

    msg = await update.message.reply_text(t(lang, "downloading"))

    try:
        media_type, path = await get_story_by_id(username, story_id)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(t(lang, "try_again"), reply_markup=main_keyboard(lang))
        return CHOOSING

    await database.log_download(user.id, f"{username}/s/{story_id}", "url")

    try:
        await msg.edit_text(t(lang, "sending", i=1, total=1))
        caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username}/s/{story_id}"
        with open(path, "rb") as f:
            if media_type == "video":
                await update.message.reply_video(video=f, caption=caption)
            else:
                await update.message.reply_photo(photo=f, caption=caption)
    finally:
        if os.path.exists(path):
            os.remove(path)

    await msg.edit_text(t(lang, "done_url"), reply_markup=main_keyboard(lang))
    return CHOOSING

# ── language selection ────────────────────────────────────────────────────────

async def choose_lang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await database.get_lang(update.callback_query.from_user.id)
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇺🇿 O'zbek",   callback_data="lang_uz"),
            InlineKeyboardButton("🇷🇺 Русский",   callback_data="lang_ru"),
            InlineKeyboardButton("🇬🇧 English",   callback_data="lang_en"),
        ],
        [InlineKeyboardButton(t(lang, "back"), callback_data="back_menu")],
    ])
    await update.callback_query.edit_message_text(t(lang, "choose_lang"), reply_markup=kb)
    return CHOOSING_LANG


async def save_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.callback_query.from_user.id
    new_lang = update.callback_query.data[len("lang_"):]   # "lang_uz" → "uz"
    await database.update_lang(user_id, new_lang)
    await update.callback_query.answer(t(new_lang, "lang_saved"), show_alert=True)
    await update.callback_query.edit_message_text(
        t(new_lang, "choose_method"), reply_markup=main_keyboard(new_lang)
    )
    return CHOOSING

# ── subscriptions ─────────────────────────────────────────────────────────────

async def show_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.callback_query.from_user.id
    lang = await database.get_lang(user_id)
    await update.callback_query.answer()
    text, kb = await _subs_view(user_id, lang)
    await update.callback_query.edit_message_text(text, reply_markup=kb)
    return CHOOSING


async def ask_add_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await database.get_lang(update.callback_query.from_user.id)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(t(lang, "enter_sub_username"))
    return WAITING_SUB_USERNAME


async def handle_sub_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    lang = await database.get_lang(user.id)
    username = update.message.text.strip().lstrip("@").lower()

    if len(username) < 3:
        await update.message.reply_text(t(lang, "enter_sub_username"))
        return WAITING_SUB_USERNAME

    await database.add_subscription(user.id, username)
    await update.message.reply_text(
        t(lang, "sub_added", username=username), reply_markup=main_keyboard(lang)
    )
    return CHOOSING


async def remove_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.callback_query.from_user.id
    lang = await database.get_lang(user_id)
    target = update.callback_query.data[len("remove_sub_"):]
    await database.remove_subscription(user_id, target)
    await update.callback_query.answer(t(lang, "sub_removed", username=target), show_alert=True)
    text, kb = await _subs_view(user_id, lang)
    await update.callback_query.edit_message_text(text, reply_markup=kb)
    return CHOOSING

# ── cancel ────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await database.get_lang(update.effective_user.id)
    await update.message.reply_text(t(lang, "cancelled"), reply_markup=main_keyboard(lang))
    return CHOOSING

# ── admin commands ────────────────────────────────────────────────────────────

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    s = await database.get_stats()
    await update.message.reply_text(
        f"📊 Statistika:\n"
        f"👥 Jami foydalanuvchilar: {s['total_users']}\n"
        f"📅 Bugun qo'shildi: {s['today_users']}\n"
        f"⬇️ Jami yuklamalar: {s['total_downloads']}\n"
        f"📅 Bugun yukladi: {s['today_downloads']}"
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    recent = await database.get_recent_users(10)
    if not recent:
        await update.message.reply_text("Bo'sh")
        return
    lines = []
    for i, u in enumerate(recent, 1):
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{i}. {u['full_name']} ({uname}) — {str(u['joined_at'])[:10]}")
    await update.message.reply_text("\n".join(lines))


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast <matn>")
        return

    user_ids = await database.get_all_user_ids()
    sent, failed = 0, 0
    progress = await update.message.reply_text(f"📤 0/{len(user_ids)} yuborilmoqda...")

    for i, uid in enumerate(user_ids, 1):
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await context.bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

        if i % 25 == 0:
            try:
                await progress.edit_text(f"📤 {i}/{len(user_ids)} yuborilmoqda...")
            except Exception:
                pass

        await asyncio.sleep(0.05)

    await progress.edit_text(
        f"✅ Broadcast tugadi!\n✅ Yuborildi: {sent}\n❌ Xato: {failed}"
    )

# ── lifecycle ─────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    await database.init_db()
    await userbot_client.start()
    start_scheduler(application.bot)
    log.info("Bot initialized")


async def post_stop(application: Application) -> None:
    stop_scheduler()
    await userbot_client.stop()
    log.info("Bot stopped")

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", show_menu)],
        states={
            CHOOSING: [
                CommandHandler("start", show_menu),
                CallbackQueryHandler(check_sub_callback,  pattern="^check_sub$"),
                CallbackQueryHandler(ask_for_username,    pattern="^by_username$"),
                CallbackQueryHandler(ask_for_url,         pattern="^by_url$"),
                CallbackQueryHandler(choose_lang_menu,    pattern="^lang_menu$"),
                CallbackQueryHandler(show_subs,           pattern="^mysubs$"),
                CallbackQueryHandler(ask_add_sub,         pattern="^add_sub$"),
                CallbackQueryHandler(remove_sub,          pattern="^remove_sub_"),
                CallbackQueryHandler(show_menu,           pattern="^back_menu$"),
            ],
            WAITING_USERNAME: [
                CommandHandler("start", show_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username),
            ],
            WAITING_URL: [
                CommandHandler("start", show_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url),
            ],
            CHOOSING_LANG: [
                CommandHandler("start", show_menu),
                CallbackQueryHandler(save_lang,  pattern="^lang_(uz|ru|en)$"),
                CallbackQueryHandler(show_menu,  pattern="^back_menu$"),
            ],
            WAITING_SUB_USERNAME: [
                CommandHandler("start", show_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sub_username),
            ],
        },
        fallbacks=[
            CommandHandler("cancel",    cancel),
            CommandHandler("start",     show_menu),
            CommandHandler("stats",     stats_cmd),
            CommandHandler("users",     users_cmd),
            CommandHandler("broadcast", broadcast_cmd),
        ],
        allow_reentry=True,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )
    application.add_handler(conv)

    print("Bot ishga tushdi!")
    application.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check all files**

```
python -c "
import ast, sys
for f in ['config.py','database.py','lang.py','userbot.py','scheduler.py','bot.py']:
    with open(f, encoding='utf-8') as fh: src = fh.read()
    try:
        ast.parse(src); print(f, 'OK')
    except SyntaxError as e:
        print(f, 'FAIL', e); sys.exit(1)
"
```

Expected: six lines each ending in `OK`.

- [ ] **Step 3: Start bot and verify in Telegram**

```
python bot.py
```

Manual checks in order:
1. `/start` → shows 4-button menu (uz by default)
2. Click "🌍 Til: O'zbek" → shows 3 language flags → click Russian → menu appears in Russian
3. Click "👤 По username" → type `@someuser` → stories download or error message shown
4. `/start` while in username-waiting state → menu resets correctly
5. Click "🔔 Мои подписки" → shows empty subs + Add button → click Add → type username → confirmation
6. `/stats` (from admin account) → shows stats
7. `/users` (from admin account) → shows list
8. `/cancel` from any state → shows menu

- [ ] **Step 4: Verify scheduler doesn't crash on startup**

Check logs within first 30 seconds for `Scheduler started` and no tracebacks.

---

## Self-Review

**Spec coverage check:**

| Requirement | Covered by |
|---|---|
| aiosqlite database | Task 2 |
| users table | Task 2 `init_db` |
| downloads table | Task 2 `init_db` + `log_download` |
| subscriptions table + `last_story_ids` | Task 2 `init_db` + `update_subscription_ids` |
| All 11 DB functions | Task 2 |
| uz/ru/en TEXTS | Task 3 |
| `t()` function | Task 3 |
| Channel subscription gate | Task 6 `is_subscribed` + `show_menu` + `check_sub_callback` |
| 4-button main menu | Task 6 `main_keyboard(lang)` |
| Language selection flow | Task 6 `choose_lang_menu` + `save_lang` |
| Language persisted to DB | Task 6 `save_lang` → `database.update_lang` |
| Subscriptions CRUD | Task 6 `show_subs`, `ask_add_sub`, `handle_sub_username`, `remove_sub` |
| APScheduler 30-min job | Task 5 `start_scheduler` |
| Scheduler sends new stories | Task 5 `_check_subscriptions` |
| First-run baseline (no spam) | Task 5 `if not last_ids: continue` |
| /stats admin command | Task 6 `stats_cmd` |
| /users admin command | Task 6 `users_cmd` |
| /broadcast with FloodWait | Task 6 `broadcast_cmd` + `RetryAfter` handler |
| post_init calls init_db | Task 6 `post_init` |
| Scheduler started in post_init | Task 6 `post_init` → `start_scheduler` |
| /cancel resets to CHOOSING | Task 6 `cancel` returns `CHOOSING` |
| `get_story_ids` for scheduler | Task 4 |
| `log_download` on each download | Task 6 `handle_username` + `handle_url` |
| Files closed with `with open` | Task 6 — all sends use context manager |

**No placeholders:** All code blocks are complete implementations.

**Type consistency:** `get_lang` → `str`, `get_stats` → `dict`, `get_all_subscriptions` → `list[dict]` with keys `user_id/username/last_story_ids` — all match between Task 2 (definition) and Tasks 5–6 (usage).
