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
import os

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
    if not story:
        raise Exception("Story topilmadi yoki o'chirilgan (yopiq yoki mavjud emas)!")

    result = await _download_media(story.media, username, story_id)
    if result:
        return result

    raise Exception(f"Bu storyda yuklab bo'ladigan media yo'q ({type(story.media).__name__})!")
