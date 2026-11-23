TEXTS: dict = {
    "welcome": (
        "🎬 *Tg Stories Downloader*\n\n"
        "📌 *How to use:*\n"
        "• *Username* — Telegram stories (last 24 hours)\n"
        "• *Telegram URL* — one specific story\n"
        "• *Instagram* — reels and videos\n\n"
        "💡 *Examples:*\n"
        "`@durov`\n"
        "`https://t.me/durov/s/123`\n"
        "`https://www.instagram.com/reel/xxx`\n\n"
        "⚠️ Only public content works!"
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
    "bad_link": "❌ Unsupported link format. Please send a Telegram @username, a Telegram story link (t.me/.../s/...) or an Instagram link.",
    "send_prompt": "👤 Send username or 🔗 Story URL",
}


def t(key: str, **kwargs) -> str:
    text = TEXTS.get(key, key)
    return text.format(**kwargs) if kwargs else text
