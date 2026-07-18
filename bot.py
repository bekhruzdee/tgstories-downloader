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
        # don't answer here — show_menu will answer the callback_query
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

    total = len(files)

    try:
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
    except Exception as e:
        for _, leftover in files:
            if os.path.exists(leftover):
                os.remove(leftover)
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(t(lang, "try_again"), reply_markup=main_keyboard(lang))
        return CHOOSING

    await database.log_download(user.id, username, "username")

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

    try:
        await msg.edit_text(t(lang, "sending", i=1, total=1))
        caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username}/s/{story_id}"
        with open(path, "rb") as f:
            if media_type == "video":
                await update.message.reply_video(video=f, caption=caption)
            else:
                await update.message.reply_photo(photo=f, caption=caption)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text(t(lang, "try_again"), reply_markup=main_keyboard(lang))
        return CHOOSING
    finally:
        if os.path.exists(path):
            os.remove(path)

    await database.log_download(user.id, f"{username}/s/{story_id}", "url")
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

    if len(username) < 5:
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

def _admin_kbd() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistika",       callback_data="adm_stats"),
            InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="adm_users_0"),
        ],
        [
            InlineKeyboardButton("⬇️ Yuklamalar", callback_data="adm_dl_0"),
            InlineKeyboardButton("🔔 Obunalar",   callback_data="adm_subs"),
        ],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
    ])


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data.pop("awaiting_broadcast", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🔐 Admin panel:", reply_markup=_admin_kbd())
    else:
        await update.message.reply_text("🔐 Admin panel:", reply_markup=_admin_kbd())


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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yangilash", callback_data="adm_stats"),
        InlineKeyboardButton("◀️ Orqaga",    callback_data="adm_menu"),
    ]])
    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    page = int(update.callback_query.data.split("_")[-1])
    per_page = 10
    users = await database.get_users_page(page, per_page)
    total = await database.get_users_count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    lines = []
    for i, u in enumerate(users, page * per_page + 1):
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(
            f"{i}. {u['full_name']} ({uname}) | ID: {u['user_id']} | "
            f"{u['lang']} | Yuklamalar: {u['download_count']}"
        )
    text = f"👥 <b>Foydalanuvchilar</b> ({total} ta)\n\n" + ("\n".join(lines) or "Bo'sh")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"adm_users_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="adm_noop"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"adm_users_{page + 1}"))
    kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("◀️ Orqaga", callback_data="adm_menu")]])
    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


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
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="adm_noop"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"adm_dl_{page + 1}"))
    kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("◀️ Orqaga", callback_data="adm_menu")]])
    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def admin_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    subs = await database.get_all_subscriptions_detailed()

    lines = []
    for i, s in enumerate(subs, 1):
        name = s["full_name"] or "—"
        lines.append(f"{i}. {name} → @{s['target_username']} | {str(s['added_at'])[:10]}")
    text = f"🔔 <b>Obunalar</b> ({len(subs)} ta)\n\n" + ("\n".join(lines) or "Bo'sh")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Orqaga", callback_data="adm_menu")]])
    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    total = await database.get_users_count()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Xabar yozish", callback_data="adm_bcast_write")],
        [InlineKeyboardButton("◀️ Orqaga",        callback_data="adm_menu")],
    ])
    await update.callback_query.edit_message_text(
        f"📢 <b>Broadcast</b>\n\nFoydalanuvchilar soni: {total}",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def admin_broadcast_write(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.answer()
    context.user_data["awaiting_broadcast"] = True
    await update.callback_query.edit_message_text(
        "✍️ Broadcast xabarini yuboring (matn yoki forward):\n\n/admin — bekor qilish"
    )


async def handle_admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_broadcast"):
        return
    context.user_data.pop("awaiting_broadcast", None)

    user_ids = await database.get_all_user_ids()
    sent, failed = 0, 0
    progress = await update.message.reply_text(f"📤 0/{len(user_ids)} yuborilmoqda...")

    for i, uid in enumerate(user_ids, 1):
        try:
            await update.message.copy_to(uid)
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await update.message.copy_to(uid)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

        if i % 10 == 0:
            try:
                await progress.edit_text(f"📤 {i}/{len(user_ids)} yuborilmoqda...")
            except Exception:
                pass

        await asyncio.sleep(0.05)

    await progress.edit_text(f"✅ Yuborildi: {sent} | ❌ Xato: {failed}")

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

    # admin panel — registered outside ConversationHandler
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CallbackQueryHandler(admin_cmd,            pattern="^adm_menu$"))
    application.add_handler(CallbackQueryHandler(admin_stats,          pattern="^adm_stats$"))
    application.add_handler(CallbackQueryHandler(admin_users,          pattern=r"^adm_users_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_downloads,      pattern=r"^adm_dl_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_subs,           pattern="^adm_subs$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^adm_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_write, pattern="^adm_bcast_write$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^adm_noop$"))
    application.add_handler(
        MessageHandler(filters.ALL & filters.User(ADMIN_ID), handle_admin_broadcast),
        group=1,
    )

    print("Bot ishga tushdi!")
    application.run_polling()


if __name__ == "__main__":
    main()
