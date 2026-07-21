TEXTS: dict = {
    "welcome": "👋 Hello! Telegram Stories downloader bot.",
    "choose_method": "📲 Choose a method:",
    "btn_username": "👤 By username",
    "btn_url": "🔗 By URL",
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
    "send_prompt": "👤 Send username or 🔗 Story URL",
}


def t(key: str, **kwargs) -> str:
    text = TEXTS.get(key, key)
    return text.format(**kwargs) if kwargs else text
