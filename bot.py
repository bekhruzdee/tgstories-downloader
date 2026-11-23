import asyncio
import logging
import os
import re
import time

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
from config import ADMIN_ID, BOT_TOKEN
from lang import t
from scheduler import start_scheduler, stop_scheduler
from userbot import (
    app as userbot_client,
    get_story_by_id,
    get_user_stories,
    download_yt_or_insta,
)

log = logging.getLogger(__name__)

CHOOSING = 0

# ── helpers ───────────────────────────────────────────────────────────────────


def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton("🔐 Admin panel", callback_data="adm_panel")])
    return InlineKeyboardMarkup(rows)


def parse_story_url(url: str) -> tuple[str | None, int | None]:
    match = re.search(r"(?:https?://)?t\.me/([^/\s]+)/s/(\d+)", url.strip())
    if match:
        return match.group(1), int(match.group(2))
    return None, None


async def _send_or_edit(update: Update, text: str, keyboard=None) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


# ── core menu ─────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("pending_broadcast", None)
    user = update.effective_user
    await database.save_user(user.id, user.username, user.full_name)
    await update.message.reply_text(t("welcome"), parse_mode="Markdown")
    await update.message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(user.id)
    )
    return CHOOSING


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await database.save_user(user.id, user.username, user.full_name)

    if update.callback_query:
        await update.callback_query.answer()

    await _send_or_edit(update, t("send_prompt"), main_keyboard(user.id))
    return CHOOSING


# ── download by username ──────────────────────────────────────────────────────


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text
    username = text.strip().lstrip("@")

    msg = await update.message.reply_text(t("downloading"))

    try:
        files = await get_user_stories(username)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING

    if not files:
        await msg.edit_text(t("no_stories"))
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING

    total = len(files)

    try:
        for i, (media_type, path) in enumerate(files, 1):
            try:
                await msg.edit_text(t("sending", i=i, total=total))
                caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username} — {i}/{total}"
                with open(path, "rb") as f:
                    if media_type == "video":
                        await update.message.reply_video(video=f, caption=caption)
                    else:
                        await update.message.reply_photo(photo=f, caption=caption)
            finally:
                if os.path.exists(path):
                    os.remove(path)
    except Exception as e:
        for _, leftover in files:
            if os.path.exists(leftover):
                os.remove(leftover)
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING

    await database.log_download(user.id, username, "username")

    await msg.edit_text(t("done_user", username=username, total=total))
    await update.message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(user.id)
    )
    return CHOOSING


# ── download by URL ───────────────────────────────────────────────────────────


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    url = update.message.text
    username, story_id = parse_story_url(url)

    if not username or not story_id:
        await update.message.reply_text(t("bad_url"))
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING

    msg = await update.message.reply_text(t("downloading"))

    try:
        media_type, path = await get_story_by_id(username, story_id)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING

    try:
        await msg.edit_text(t("sending", i=1, total=1))
        caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username}/s/{story_id}"
        with open(path, "rb") as f:
            if media_type == "video":
                await update.message.reply_video(video=f, caption=caption)
            else:
                await update.message.reply_photo(photo=f, caption=caption)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(
            t("send_prompt"), reply_markup=main_keyboard(user.id)
        )
        return CHOOSING
    finally:
        if os.path.exists(path):
            os.remove(path)

    await database.log_download(user.id, f"{username}/s/{story_id}", "url")
    await msg.edit_text(t("done_url"))
    await update.message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(user.id)
    )
    return CHOOSING


# ── smart text router ────────────────────────────────────────────────────────


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await database.save_user(user.id, user.username, user.full_name)
    if context.user_data.get("awaiting_broadcast"):
        await handle_admin_broadcast(update, context)
        return CHOOSING

    user_id = update.effective_user.id
    now = time.time()
    rate = context.bot_data.setdefault("rate_limit", {})
    timestamps = [ts for ts in rate.get(user_id, []) if now - ts < 60]
    if len(timestamps) >= 3:
        wait = int(60 - (now - timestamps[0]))
        await update.message.reply_text(
            f"⏳ Too many requests! Please wait {wait} seconds."
        )
        return CHOOSING
    timestamps.append(now)
    rate[user_id] = timestamps

    text = update.message.text
    text_l = text.strip().lower()
    is_link = text_l.startswith(("http://", "https://", "www.")) or "://" in text_l
    if is_link and "instagram.com" not in text_l and "t.me/" not in text_l:
        await update.message.reply_text(t("bad_link"))
        await update.message.reply_text(t("send_prompt"), reply_markup=main_keyboard(user.id))
        return CHOOSING
    if "instagram.com" in text:
        return await handle_yt_insta(update, context)
    if "t.me/" in text:
        return await handle_url(update, context)
    return await handle_username(update, context)


async def handle_yt_insta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    url = update.message.text.strip()
    msg = await update.message.reply_text("⏳ Downloading... this can take up to a minute.")
    t0 = time.time()
    try:
        media_type, path = await download_yt_or_insta(url)
        log.warning("IG download took %.1fs", time.time() - t0)
        await msg.edit_text("📤 Sending...")
        t1 = time.time()
        with open(path, "rb") as f:
            if media_type == "video":
                await update.message.reply_video(video=f, caption=f"🎬 {url[:50]}")
            else:
                await update.message.reply_photo(photo=f, caption=f"🖼 {url[:50]}")
        log.warning("IG send took %.1fs", time.time() - t1)
        os.remove(path)
    except Exception as e:
        log.warning("IG error: %s", e)
        error_msg = str(e).lower()
        if any(x in error_msg for x in ["no video", "no video formats", "empty media", "photo"]):
            await msg.edit_text(
                "📸 Instagram photos cannot be downloaded.\n"
                "✅ Only Instagram videos and reels work!"
            )
        elif any(x in error_msg for x in ["login", "private", "unavailable"]):
            await msg.edit_text("❌ This post is private or unavailable.")
        else:
            await msg.edit_text("❌ Could not download this link. Please try again later.")
    await update.message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(user.id)
    )
    return CHOOSING


# ── cancel ────────────────────────────────────────────────────────────────────


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("pending_broadcast", None)
    user_id = update.effective_user.id
    await update.message.reply_text(t("cancelled"))
    await update.message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(user_id)
    )
    return CHOOSING


# ── admin commands ────────────────────────────────────────────────────────────


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return CHOOSING
    s = await database.get_stats()
    await update.message.reply_text(
        f"📊 Statistika:\n"
        f"👥 Jami foydalanuvchilar: {s['total_users']}\n"
        f"📅 Bugun qo'shildi: {s['today_users']}\n"
        f"⬇️ Jami yuklamalar: {s['total_downloads']}\n"
        f"📅 Bugun yukladi: {s['today_downloads']}"
    )
    return CHOOSING


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return CHOOSING
    recent = await database.get_recent_users(10)
    if not recent:
        await update.message.reply_text("Bo'sh")
        return CHOOSING
    lines = []
    for i, u in enumerate(recent, 1):
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{i}. {u['full_name']} ({uname}) — {str(u['joined_at'])[:10]}")
    await update.message.reply_text("\n".join(lines))
    return CHOOSING


async def _do_broadcast(bot, text: str, user_ids: list[int], progress) -> None:
    sent, failed = 0, 0
    for i, uid in enumerate(user_ids, 1):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text)
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


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
        return CHOOSING
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast <matn>")
        return CHOOSING

    user_ids = await database.get_all_user_ids()
    progress = await update.message.reply_text(f"📤 0/{len(user_ids)} yuborilmoqda...")
    asyncio.create_task(_do_broadcast(context.bot, text, user_ids, progress))
    return CHOOSING


# ── admin panel ───────────────────────────────────────────────────────────────


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Statistika", callback_data="adm_stats"),
                InlineKeyboardButton(
                    "👥 Foydalanuvchilar", callback_data="adm_users_0"
                ),
            ],
            [InlineKeyboardButton("⬇️ Yuklamalar", callback_data="adm_dl_0")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("◀️ Orqaga", callback_data="adm_menu")],
        ]
    )
    await update.callback_query.edit_message_text("🔐 Admin panel:", reply_markup=kb)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    s = await database.get_stats_extended()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 <b>Foydalanuvchilar:</b>\n"
        f"  Jami: {s['total_users']}\n"
        f"  Bugun: {s['today_users']}\n"
        f"  Hafta: {s['week_users']}\n"
        f"  Oy: {s['month_users']}\n\n"
        f"⬇️ <b>Yuklamalar:</b>\n"
        f"  Jami: {s['total_downloads']}\n"
        f"  Bugun: {s['today_downloads']}\n"
        f"  Username: {s['username_downloads']}\n"
        f"  URL: {s['url_downloads']}\n\n"
        f"🔔 <b>Obunalar:</b> {s['total_subscriptions']}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Yangilash", callback_data="adm_stats"),
                InlineKeyboardButton("◀️ Orqaga", callback_data="adm_panel"),
            ]
        ]
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=kb, parse_mode="HTML"
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    page = int(update.callback_query.data.split("_")[-1])
    per_page = 10
    users = await database.get_users_page(page, per_page)
    total = await database.get_users_count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    log.info("admin_users page=%d, count=%d, total=%d", page, len(users), total)
    lines = []
    for i, u in enumerate(users, page * per_page + 1):
        uname = f"@{u['username']}" if u["username"] else "—"
        joined = str(u["joined_at"])[:10] if u["joined_at"] else "—"
        lines.append(
            f"{i}. {u['full_name']} ({uname})\n"
            f"🆔 {u['user_id']} | 📅 {joined} | ⬇️ {u['download_count']} ta"
        )
    text = f"👥 <b>Foydalanuvchilar</b> ({total} ta)\n\n" + (
        "\n\n".join(lines) or "Bo'sh"
    )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("◀️ Prev", callback_data=f"adm_users_{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="adm_noop")
    )
    if (page + 1) * per_page < total:
        nav.append(
            InlineKeyboardButton("Next ▶️", callback_data=f"adm_users_{page + 1}")
        )
    kb = InlineKeyboardMarkup(
        [nav, [InlineKeyboardButton("◀️ Orqaga", callback_data="adm_panel")]]
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=kb, parse_mode="HTML"
    )


async def admin_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    page = int(update.callback_query.data.split("_")[-1])
    per_page = 20
    downloads = await database.get_recent_downloads(page, per_page)
    total = await database.get_downloads_count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    lines = []
    for i, d in enumerate(downloads, page * per_page + 1):
        name = d["full_name"] or "—"
        lines.append(
            f"{i}. {name} → @{d['target']} | {d['type']} | {str(d['downloaded_at'])[:16]}"
        )
    text = f"⬇️ <b>Yuklamalar</b> ({total} ta)\n\n" + ("\n".join(lines) or "Bo'sh")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"adm_dl_{page - 1}"))
    nav.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="adm_noop")
    )
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"adm_dl_{page + 1}"))
    kb = InlineKeyboardMarkup(
        [nav, [InlineKeyboardButton("◀️ Orqaga", callback_data="adm_panel")]]
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=kb, parse_mode="HTML"
    )


async def admin_broadcast_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    context.user_data["awaiting_broadcast"] = True
    await update.callback_query.edit_message_text(
        "Xabar yuboring (matn, rasm, video, ovozli xabar — hammasi qabul qilinadi):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_bc_cancel")]]),
    )


async def run_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int
) -> None:
    user_ids = await database.get_all_user_ids()
    log.info("Broadcast starting: %d users", len(user_ids))

    sent, failed = 0, 0
    progress_msg = await update.effective_message.reply_text(
        f"📤 Yuborilmoqda: 0/{len(user_ids)}..."
    )

    for i, uid in enumerate(user_ids):
        try:
            uid = int(uid)
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=chat_id,
                message_id=message_id,
            )
            sent += 1
        except Exception as e:
            failed += 1
            log.warning("Broadcast failed uid=%s: %s", uid, e)

        if i % 10 == 0:
            try:
                await progress_msg.edit_text(
                    f"📤 Yuborilmoqda: {i + 1}/{len(user_ids)}..."
                )
            except Exception:
                pass

        await asyncio.sleep(0.05)

    await progress_msg.edit_text(f"✅ Yuborildi: {sent} | ❌ Xato: {failed}")
    await update.effective_message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(update.effective_user.id)
    )


async def handle_admin_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not context.user_data.get("awaiting_broadcast"):
        return
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data["pending_broadcast"] = (update.message.chat.id, update.message.message_id)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Send", callback_data="adm_bc_send"),
            InlineKeyboardButton("❌ Cancel", callback_data="adm_bc_cancel"),
        ]
    ])
    await update.message.reply_text("Send this message to all users?", reply_markup=kb)


async def adm_bc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("pending_broadcast", None)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Broadcast cancelled.")
    await update.effective_message.reply_text(
        t("send_prompt"), reply_markup=main_keyboard(update.effective_user.id)
    )


async def adm_bc_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    pending = context.user_data.pop("pending_broadcast", None)
    if not pending:
        await update.callback_query.answer("No pending broadcast.")
        return
    chat_id, message_id = pending
    await update.callback_query.answer()
    await run_broadcast(update, context, chat_id, message_id)


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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input),
        ],
        states={
            CHOOSING: [
                CommandHandler("start", cmd_start),
                CallbackQueryHandler(show_menu, pattern="^back_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", cmd_start),
            CommandHandler("stats", stats_cmd),
            CommandHandler("users", users_cmd),
            CommandHandler("broadcast", broadcast_cmd),
        ],
        allow_reentry=True,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )
    application.add_handler(conv)

    # admin panel — registered outside ConversationHandler
    application.add_handler(CallbackQueryHandler(show_menu, pattern="^adm_menu$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^adm_panel$"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^adm_stats$"))
    application.add_handler(
        CallbackQueryHandler(admin_users, pattern=r"^adm_users_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(admin_downloads, pattern=r"^adm_dl_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(admin_broadcast_menu, pattern="^adm_broadcast$")
    )
    application.add_handler(CallbackQueryHandler(adm_bc_cancel, pattern="^adm_bc_cancel$"))
    application.add_handler(CallbackQueryHandler(adm_bc_send, pattern="^adm_bc_send$"))
    application.add_handler(
        CallbackQueryHandler(
            lambda u, c: u.callback_query.answer(), pattern="^adm_noop$"
        )
    )
    log.info("Bot ishga tushdi!")
    application.run_polling()


if __name__ == "__main__":
    main()
