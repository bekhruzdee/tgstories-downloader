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
                path = None
                try:
                    media_type, path = await get_story_by_id(username, story_id)
                    caption = t(lang, "sub_notify", username=username)
                    with open(path, "rb") as f:
                        if media_type == "video":
                            await _bot.send_video(user_id, f, caption=caption)
                        else:
                            await _bot.send_photo(user_id, f, caption=caption)
                except Exception as e:
                    log.error(
                        "Scheduler: failed to send story %s/%s to %s: %s",
                        username,
                        story_id,
                        user_id,
                        e,
                    )
                finally:
                    if path and os.path.exists(path):
                        os.remove(path)

        except Exception as e:
            log.error(
                "Scheduler: error processing sub %s for user %s: %s",
                username,
                user_id,
                e,
            )

        await asyncio.sleep(0.3)  # avoid flood between users


def start_scheduler(bot) -> None:
    global _bot
    _bot = bot
    os.makedirs("stories", exist_ok=True)
    _scheduler.add_job(_check_subscriptions, "interval", minutes=30, id="sub_check")
    _scheduler.start()
    log.info("Scheduler started (30-min interval)")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
