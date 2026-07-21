TEXTS: dict = {
    "welcome": (
        "🎬 *Tg Stories Downloader*\n\n"
        "Download Telegram users' stories easily!\n\n"
        "📌 *How to use:*\n"
        "• *Username* — download currently visible stories (last 24 hours)\n"
        "• *URL* — download one specific story\n\n"
        "💡 *Example:*\n"
        "username: `@durov`\n"
        "URL: `https://t.me/durov/s/123`\n\n"
        "⚠️ Only works with public profiles!\n"
        "⏳ Stories disappear after 24 hours"
    ),
    "downloading": "⏳ Downloading stories...",
    "sending": "📤 Sending {i}/{total}...",
    "done_user": "✅ Sent {total} stories from @{username}!",
    "done_url": "✅ Story sent successfully!",
    "no_stories": "😕 No active stories.",
    "cancelled": "❌ Cancelled.",
    "private": "🔒 This profile is private!",
    "not_found": "❌ Username not found!",
    "back": "⬅️ Back",
    "try_again": "Try again:",
    "bad_url": "❌ Invalid URL format!\n\nExample: https://t.me/durov/s/123",
    "send_prompt": "👤 Send username or 🔗 Story URL",
}


def t(key: str, **kwargs) -> str:
    text = TEXTS.get(key, key)
    return text.format(**kwargs) if kwargs else text
