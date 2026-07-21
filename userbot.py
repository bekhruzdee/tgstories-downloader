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


async def _download_story(story, username: str, story_id: int) -> tuple:
    """Try every known approach to download a single story object."""
    print(f"[DEBUG] _download_story: type={type(story).__name__}, id={story_id}")
    print(f"[DEBUG] _download_story: story.__dict__ = {story.__dict__}")

    # Approach 1: direct story download
    try:
        path = await app.download_media(story, file_name=f"stories/{username}_{story_id}")
        if path:
            is_video = path.endswith('.mp4') or path.endswith('.mov')
            print(f"[DEBUG] Approach 1 (direct story) succeeded: {path}")
            return ("video" if is_video else "photo", path)
        print("[DEBUG] Approach 1 returned None path")
    except Exception as e1:
        print(f"[DEBUG] Approach 1 (direct story) failed: {e1}")
        traceback.print_exc()

    # Approach 2: story.photo
    try:
        if hasattr(story, 'photo') and story.photo:
            path = await app.download_media(story.photo, file_name=f"stories/{username}_{story_id}.jpg")
            if path:
                print(f"[DEBUG] Approach 2 (story.photo) succeeded: {path}")
                return ("photo", path)
            print("[DEBUG] Approach 2 returned None path")
        else:
            print("[DEBUG] Approach 2 skipped: no photo attr")
    except Exception as e2:
        print(f"[DEBUG] Approach 2 (story.photo) failed: {e2}")
        traceback.print_exc()

    # Approach 3: story.video
    try:
        if hasattr(story, 'video') and story.video:
            path = await app.download_media(story.video, file_name=f"stories/{username}_{story_id}.mp4")
            if path:
                print(f"[DEBUG] Approach 3 (story.video) succeeded: {path}")
                return ("video", path)
            print("[DEBUG] Approach 3 returned None path")
        else:
            print("[DEBUG] Approach 3 skipped: no video attr")
    except Exception as e3:
        print(f"[DEBUG] Approach 3 (story.video) failed: {e3}")
        traceback.print_exc()

    # Approach 4: story.media
    try:
        if hasattr(story, 'media') and story.media:
            path = await app.download_media(story.media, file_name=f"stories/{username}_{story_id}")
            if path:
                is_video = path.endswith('.mp4') or path.endswith('.mov')
                print(f"[DEBUG] Approach 4 (story.media) succeeded: {path}")
                return ("video" if is_video else "photo", path)
            print("[DEBUG] Approach 4 returned None path")
        else:
            print("[DEBUG] Approach 4 skipped: no media attr")
    except Exception as e4:
        print(f"[DEBUG] Approach 4 (story.media) failed: {e4}")
        traceback.print_exc()

    raise Exception("Media yuklab bo'lmadi!")


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
            result = await _download_story(story, username, story.id)
            downloaded.append(result)
            print(f"[DEBUG] OK  story {story.id} → {result[0]}: {result[1]}")
        except Exception as e:
            print(f"[DEBUG] FAIL story {story.id}: {e}")

    print(f"[DEBUG] Total downloaded: {len(downloaded)}")
    return downloaded


async def get_story_by_id(username: str, story_id: int) -> tuple:
    os.makedirs("stories", exist_ok=True)
    print(f"[DEBUG] get_story_by_id called: username={username}, story_id={story_id}")

    try:
        peer = await app.resolve_peer(username)
        result = await app.invoke(GetStoriesByID(peer=peer, id=[story_id]))
    except UserPrivacyRestricted:
        raise Exception("Bu foydalanuvchi profili yopiq!")
    except PeerIdInvalid:
        raise Exception("Bunday username topilmadi!")
    except Exception as e:
        print(f"[DEBUG] API error: {type(e).__name__}: {e}")
        raise Exception(f"Xatolik: {e}")

    print(f"[DEBUG] All stories: {result.stories}")
    for s in result.stories:
        print(f"[DEBUG] Story type: {type(s).__name__}, id: {s.id}")

    story = next((s for s in result.stories if s.id == story_id), None)
    if story is None:
        raise Exception("Story topilmadi yoki o'chirilgan!")

    return await _download_story(story, username, story_id)


async def get_story_ids(username: str) -> list[int]:
    """Return IDs of all accessible active stories for a user without downloading."""
    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
        return [s.id for s in r.stories.stories if isinstance(s, StoryItem)]
    except Exception as e:
        log.warning("get_story_ids(%s): %s: %s", username, type(e).__name__, e)
        return []
