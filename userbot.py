import logging
import os
import traceback

from pyrogram import Client, types
from pyrogram.raw.functions.stories import GetPeerStories, GetStoriesByID
from pyrogram.raw.types import (
    StoryItem,
    StoryItemSkipped,
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    Document,
)
from pyrogram.errors import UserPrivacyRestricted, PeerIdInvalid
from config import API_ID, API_HASH

log = logging.getLogger(__name__)

app = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)


def _to_photo(raw_photo) -> "types.Photo | None":
    try:
        return types.Photo._parse(app, raw_photo, ttl_seconds=None)
    except Exception as e:
        print(f"[DEBUG] _to_photo failed: {e}")
        return None


def _to_video(raw_doc) -> "types.Video | None":
    try:
        attrs = {type(a): a for a in raw_doc.attributes}
        video_attr = attrs.get(DocumentAttributeVideo)
        if not video_attr:
            return None
        return types.Video._parse(app, raw_doc, video_attr, file_name=None)
    except Exception as e:
        print(f"[DEBUG] _to_video failed: {e}")
        return None


async def _download_media(raw_media, username: str, story_id: int) -> "tuple | None":
    if isinstance(raw_media, MessageMediaPhoto):
        photo = _to_photo(raw_media.photo)
        if photo:
            path = await app.download_media(
                photo, file_name=f"stories/{username}_{story_id}.jpg"
            )
            return ("photo", path) if path else None

    elif isinstance(raw_media, MessageMediaDocument):
        doc = raw_media.document
        if isinstance(doc, Document):
            video = _to_video(doc)
            if video:
                path = await app.download_media(
                    video, file_name=f"stories/{username}_{story_id}.mp4"
                )
                return ("video", path) if path else None

    return None


async def _download_story(story, username: str, story_id: int) -> "tuple | None":
    """Try every known approach to download a single StoryItem."""
    raw_media = getattr(story, "media", None)
    print(f"[DEBUG] _download_story: media type = {type(raw_media).__name__}")
    if raw_media and hasattr(raw_media, "__dict__"):
        print(f"[DEBUG] _download_story: media.__dict__ = {raw_media.__dict__}")

    # Approach 1: pass the story object directly to download_media 1
    try:
        path = await app.download_media(story, file_name=f"stories/{username}_{story_id}")
        if path:
            ext = os.path.splitext(path)[1].lower()
            media_type = "video" if ext in (".mp4", ".mov", ".avi", ".mkv") else "photo"
            print(f"[DEBUG] Approach 1 (direct story) succeeded: {path}")
            return (media_type, path)
        print("[DEBUG] Approach 1 returned None path")
    except Exception as e:
        print(f"[DEBUG] Approach 1 (direct story) failed: {e}")
        traceback.print_exc()

    # Approach 2: pass story.media directly to download_media
    if raw_media is not None:
        try:
            path = await app.download_media(raw_media, file_name=f"stories/{username}_{story_id}")
            if path:
                ext = os.path.splitext(path)[1].lower()
                media_type = "video" if ext in (".mp4", ".mov", ".avi", ".mkv") else "photo"
                print(f"[DEBUG] Approach 2 (story.media direct) succeeded: {path}")
                return (media_type, path)
            print("[DEBUG] Approach 2 returned None path")
        except Exception as e:
            print(f"[DEBUG] Approach 2 (story.media direct) failed: {e}")
            traceback.print_exc()

    # Approach 3: typed conversion (MessageMediaPhoto / MessageMediaDocument)
    if raw_media is not None:
        try:
            result = await _download_media(raw_media, username, story_id)
            if result:
                print(f"[DEBUG] Approach 3 (typed conversion) succeeded: {result}")
                return result
            print("[DEBUG] Approach 3 returned None")
        except Exception as e:
            print(f"[DEBUG] Approach 3 (typed conversion) failed: {e}")
            traceback.print_exc()

    return None


async def _resolve_skipped(peer, story_ids: list) -> "list[StoryItem]":
    """Try to upgrade StoryItemSkipped → StoryItem via GetStoriesByID."""
    try:
        r = await app.invoke(GetStoriesByID(peer=peer, id=story_ids))
        full = [s for s in r.stories if isinstance(s, StoryItem)]
        print(f"[DEBUG] GetStoriesByID: got {len(full)}/{len(story_ids)} full stories")
        return full
    except Exception as e:
        print(f"[DEBUG] GetStoriesByID failed: {type(e).__name__}: {e}")
        return []


async def get_user_stories(username: str) -> list:
    os.makedirs("stories", exist_ok=True)

    # ── API call ──────────────────────────────────────────────────────────────
    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
    except UserPrivacyRestricted:
        raise Exception("Bu foydalanuvchi profili yopiq!")
    except PeerIdInvalid:
        raise Exception("Bunday username topilmadi!")
    except Exception as e:
        print(f"[DEBUG] API error: {type(e).__name__}: {e}")
        raise Exception(f"Xatolik: {e}")

    # r                 → types.stories.PeerStories  (outer wrapper)
    # r.stories         → types.PeerStories          (inner per-peer object)
    # r.stories.stories → List[StoryItem | StoryItemSkipped | StoryItemDeleted]
    raw_list = r.stories.stories
    print(f"[DEBUG] GetPeerStories returned {len(raw_list)} items")
    for s in raw_list:
        print(f"[DEBUG]   {type(s).__name__} id={s.id}")

    # ── Separate full vs skipped ──────────────────────────────────────────────
    full_stories: list[StoryItem] = []
    skipped_ids: list[int] = []

    for s in raw_list:
        if isinstance(s, StoryItem):
            full_stories.append(s)
        elif isinstance(s, StoryItemSkipped):
            # Telegram hides media when userbot doesn't follow the user;
            # try GetStoriesByID — works for public stories, fails for private
            skipped_ids.append(s.id)

    if skipped_ids:
        print(f"[DEBUG] {len(skipped_ids)} skipped stories — trying GetStoriesByID")
        full_stories.extend(await _resolve_skipped(peer, skipped_ids))

    # ── Download ───────────────────────────────────────────────────────────────
    downloaded = []
    for story in full_stories:
        try:
            result = await _download_media(story.media, username, story.id)
            if result:
                downloaded.append(result)
                print(f"[DEBUG] OK  story {story.id} → {result[0]}: {result[1]}")
            else:
                print(
                    f"[DEBUG] SKIP story {story.id}: "
                    f"unsupported media type ({type(story.media).__name__})"
                )
        except Exception as e:
            print(f"[DEBUG] FAIL story {story.id}: {e}")

    print(f"[DEBUG] Total downloaded: {len(downloaded)}")
    return downloaded


async def get_story_by_id(username: str, story_id: int) -> tuple:
    os.makedirs("stories", exist_ok=True)
    print(f"[DEBUG] get_story_by_id called: username={username}, story_id={story_id}")

    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetStoriesByID(peer=peer, id=[story_id]))
    except UserPrivacyRestricted:
        raise Exception("Bu foydalanuvchi profili yopiq!")
    except PeerIdInvalid:
        raise Exception("Bunday username topilmadi!")
    except Exception as e:
        print(f"[DEBUG] API error: {type(e).__name__}: {e}")
        raise Exception(f"Xatolik: {e}")

    # r         → types.stories.Stories
    # r.stories → List[StoryItem | StoryItemSkipped | ...]  (direct list)
    print(f"[DEBUG] GetStoriesByID returned {len(r.stories)} items")
    for s in r.stories:
        print(f"[DEBUG]   {type(s).__name__} id={s.id}")

    story = next((s for s in r.stories if isinstance(s, StoryItem)), None)
    print(f"[DEBUG] Story result: {story}")
    print(f"[DEBUG] Story type: {type(story)}")
    if story:
        print(f"[DEBUG] Story attributes: {dir(story)}")
    if not story:
        raise Exception("Story topilmadi yoki o'chirilgan (yopiq yoki mavjud emas)!")

    result = await _download_story(story, username, story_id)
    if result:
        return result

    raise Exception(f"Bu storyda yuklab bo'ladigan media yo'q ({type(getattr(story, 'media', None)).__name__})!")


async def get_story_ids(username: str) -> list[int]:
    """Return IDs of all accessible active stories for a user without downloading."""
    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
        return [s.id for s in r.stories.stories if isinstance(s, StoryItem)]
    except Exception as e:
        log.warning("get_story_ids(%s): %s: %s", username, type(e).__name__, e)
        return []


import yt_dlp as _yt_dlp
import glob


async def download_yt_or_insta(url: str) -> tuple:
    os.makedirs("downloads", exist_ok=True)
    opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'format': 'best[filesize<50M]/best/bestvideo+bestaudio',
        'quiet': True,
        'merge_output_format': 'mp4',
        'extractor_args': {
            'youtube': {'player_client': ['android']},
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36',
        },
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    }
    with _yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get('id') or info.get('webpage_url_basename', 'video')
        def _typed(path: str) -> tuple:
            ext = os.path.splitext(path)[1].lower()
            media_type = "photo" if ext in ('.jpg', '.jpeg', '.png', '.webp') else "video"
            return (media_type, path)

        direct = ydl.prepare_filename(info)
        if os.path.exists(direct):
            return _typed(direct)
        files = glob.glob(f"downloads/{video_id}.*")
        if files:
            return _typed(files[0])
        files = sorted(glob.glob("downloads/*"), key=os.path.getmtime, reverse=True)
        if files:
            return _typed(files[0])
        raise Exception("File not found after download")
