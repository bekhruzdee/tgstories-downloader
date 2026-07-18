import re
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from userbot import app as userbot_client, get_user_stories, get_story_by_id
from config import BOT_TOKEN

CHOOSING, WAITING_USERNAME, WAITING_URL = range(3)


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Username orqali", callback_data="by_username")],
        [InlineKeyboardButton("🔗 URL orqali", callback_data="by_url")],
    ])


def parse_story_url(url: str) -> tuple[str | None, int | None]:
    match = re.search(r'(?:https?://)?t\.me/([^/\s]+)/s/(\d+)', url.strip())
    if match:
        return match.group(1), int(match.group(2))
    return None, None


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "📲 Quyidagi usullardan birini tanlang:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=main_keyboard())
    return CHOOSING


async def ask_for_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "👤 Username kiriting:\n\nMisol: @durov yoki durov\n\n/cancel — bekor qilish"
    )
    return WAITING_USERNAME


async def ask_for_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🔗 Story URL ni yuboring:\n\nMisol: https://t.me/durov/s/123\n\n/cancel — bekor qilish"
    )
    return WAITING_URL


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.message.text.strip().lstrip("@")
    msg = await update.message.reply_text(f"⏳ @{username} ning storylari yuklanmoqda...")

    try:
        files = await get_user_stories(username)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text("Boshqa harakat qiling:", reply_markup=main_keyboard())
        return CHOOSING

    if not files:
        await msg.edit_text(f"😕 @{username} ning hozirda faol storiysi yo'q.")
        await update.message.reply_text("Boshqa harakat qiling:", reply_markup=main_keyboard())
        return CHOOSING

    total = len(files)
    for i, (media_type, path) in enumerate(files, 1):
        try:
            await msg.edit_text(f"📤 {i}/{total} yuborilmoqda...")
            caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username} — {i}/{total}"
            if media_type == "video":
                await update.message.reply_video(video=open(path, "rb"), caption=caption)
            else:
                await update.message.reply_photo(photo=open(path, "rb"), caption=caption)
        finally:
            if os.path.exists(path):
                os.remove(path)

    await msg.edit_text(
        f"✅ @{username} ning {total} ta storiysi yuborildi!",
        reply_markup=main_keyboard(),
    )
    return CHOOSING


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username, story_id = parse_story_url(update.message.text)

    if not username or not story_id:
        await update.message.reply_text(
            "❌ Noto'g'ri URL format!\n\nMisol: https://t.me/durov/s/123"
        )
        return WAITING_URL

    msg = await update.message.reply_text("⏳ Story yuklanmoqda...")

    try:
        media_type, path = await get_story_by_id(username, story_id)
    except Exception as e:
        await msg.edit_text(f"❌ {e}")
        await update.message.reply_text("Boshqa harakat qiling:", reply_markup=main_keyboard())
        return CHOOSING

    try:
        await msg.edit_text("📤 Yuborilmoqda...")
        caption = f"{'🎬' if media_type == 'video' else '🖼'} @{username}/s/{story_id}"
        if media_type == "video":
            await update.message.reply_video(video=open(path, "rb"), caption=caption)
        else:
            await update.message.reply_photo(photo=open(path, "rb"), caption=caption)
    finally:
        if os.path.exists(path):
            os.remove(path)

    await msg.edit_text("✅ Story muvaffaqiyatli yuborildi!", reply_markup=main_keyboard())
    return CHOOSING


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def post_init(application: Application) -> None:
    await userbot_client.start()
    print("Userbot ulandi!")


async def post_stop(application: Application) -> None:
    await userbot_client.stop()
    print("Userbot uzildi!")


def main() -> None:
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", show_menu),
        ],
        states={
            CHOOSING: [
                CommandHandler("start", show_menu),
                CallbackQueryHandler(ask_for_username, pattern="^by_username$"),
                CallbackQueryHandler(ask_for_url, pattern="^by_url$"),
            ],
            WAITING_USERNAME: [
                CommandHandler("start", show_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username),
            ],
            WAITING_URL: [
                CommandHandler("start", show_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", show_menu),
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
