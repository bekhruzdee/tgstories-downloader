import logging
import os

from pyrogram import Client
from pyrogram.raw.functions.stories import GetPeerStories, GetStoriesByID
from pyrogram.raw.types import (
    StoryItem,
    StoryItemSkipped,
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    Document,
    Photo,
)
from pyrogram.errors import UserPrivacyRestricted, PeerIdInvalid
from config import API_ID, API_HASH

log = logging.getLogger(__name__)

app = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)


async def _download_media(raw_media, username: str, story_id: int):
    """Download from raw MessageMediaPhoto or MessageMediaDocument."""
    try:
        if isinstance(raw_media, MessageMediaPhoto):
            raw_photo = raw_media.photo
            if isinstance(raw_photo, Photo):
                path = await app.download_media(
                    raw_photo,
                    file_name=f"stories/{username}_{story_id}.jpg"
                )
                if path:
                    return ("photo", path)

        elif isinstance(raw_media, MessageMediaDocument):
            doc = raw_media.document
            if isinstance(doc, Document):
                is_video = any(
                    isinstance(a, DocumentAttributeVideo)
                    for a in doc.attributes
                )
                ext = ".mp4" if is_video else ".bin"
                path = await app.download_media(
                    doc,
                    file_name=f"stories/{username}_{story_id}{ext}"
                )
                if path:
                    return ("video" if is_video else "photo", path)
    except Exception as e:
        print(f"[DEBUG] _download_media failed: {e}")

    return None


async def _download_story(story, username: str, story_id: int) -> tuple:
    print(f"[DEBUG] _download_story: type={type(story).__name__}, id={story_id}")

    # Approach 0: story.media (correct for raw StoryItem)
    try:
        media = getattr(story, 'media', None)
        if media:
            result = await _download_media(media, username, story_id)
            if result:
                print(f"[DEBUG] Approach 0 succeeded: {result[0]}")
                return result
    except Exception as e:
        print(f"[DEBUG] Approach 0 failed: {e}")

    # Approach 1: direct download
    try:
        path = await app.download_media(
            story,
            file_name=f"stories/{username}_{story_id}"
        )
        if path:
            is_video = path.endswith('.mp4') or path.endswith('.mov')
            print(f"[DEBUG] Approach 1 succeeded: {path}")
            return ("video" if is_video else "photo", path)
    except Exception as e:
        print(f"[DEBUG] Approach 1 failed: {e}")

    # Approach 2: story.photo
    try:
        photo = getattr(story, 'photo', None)
        if photo:
            path = await app.download_media(
                photo,
                file_name=f"stories/{username}_{story_id}.jpg"
            )
            if path:
                print(f"[DEBUG] Approach 2 succeeded: {path}")
                return ("photo", path)
    except Exception as e:
        print(f"[DEBUG] Approach 2 failed: {e}")

    # Approach 3: story.video
    try:
        video = getattr(story, 'video', None)
        if video:
            path = await app.download_media(
                video,
                file_name=f"stories/{username}_{story_id}.mp4"
            )
            if path:
                print(f"[DEBUG] Approach 3 succeeded: {path}")
                return ("video", path)
    except Exception as e:
        print(f"[DEBUG] Approach 3 failed: {e}")

    raise Exception("Media yuklab bo'lmadi!")


async def _resolve_skipped(peer, story_ids: list) -> list:
    """Try to get full StoryItem from skipped ones via GetStoriesByID."""
    try:
        r = await app.invoke(GetStoriesByID(peer=peer, id=story_ids))
        full = [s for s in r.stories if isinstance(s, StoryItem)]
        print(f"[DEBUG] _resolve_skipped: {len(full)}/{len(story_ids)} resolved")
        return full
    except Exception as e:
        print(f"[DEBUG] _resolve_skipped failed: {e}")
        return []


async def get_user_stories(username: str) -> list:
    os.makedirs("stories", exist_ok=True)

    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
    except UserPrivacyRestricted:
        raise Exception("Bu foydalanuvchi profili yopiq!")
    except PeerIdInvalid:
        raise Exception("Bunday username topilmadi!")
    except Exception as e:
        raise Exception(f"Xatolik: {e}")

    raw_list = r.stories.stories
    print(f"[DEBUG] get_user_stories: {len(raw_list)} stories found")

    full_stories = []
    skipped_ids = []

    for s in raw_list:
        if isinstance(s, StoryItem):
            full_stories.append(s)
        elif isinstance(s, StoryItemSkipped):
            skipped_ids.append(s.id)

    if skipped_ids:
        print(f"[DEBUG] {len(skipped_ids)} skipped — resolving...")
        full_stories.extend(await _resolve_skipped(peer, skipped_ids))

    downloaded = []
    for story in full_stories:
        try:
            result = await _download_story(story, username, story.id)
            downloaded.append(result)
        except Exception as e:
            print(f"[DEBUG] Failed story {story.id}: {e}")

    print(f"[DEBUG] Total downloaded: {len(downloaded)}")
    return downloaded


async def get_story_by_id(username: str, story_id: int) -> tuple:
    os.makedirs("stories", exist_ok=True)
    print(f"[DEBUG] get_story_by_id: {username}, {story_id}")

    try:
        peer = await app.resolve_peer(username)
        result = await app.invoke(GetStoriesByID(peer=peer, id=[story_id]))
    except UserPrivacyRestricted:
        raise Exception("Bu foydalanuvchi profili yopiq!")
    except PeerIdInvalid:
        raise Exception("Bunday username topilmadi!")
    except Exception as e:
        raise Exception(f"Xatolik: {e}")

    print(f"[DEBUG] get_story_by_id: {len(result.stories)} results")
    for s in result.stories:
        print(f"[DEBUG]   {type(s).__name__} id={s.id}")

    story = next((s for s in result.stories if s.id == story_id), None)
    if story is None:
        raise Exception("Story topilmadi yoki o'chirilgan!")

    return await _download_story(story, username, story_id)


async def get_story_ids(username: str) -> list:
    try:
        peer = await app.resolve_peer(username)
        r = await app.invoke(GetPeerStories(peer=peer))
        return [s.id for s in r.stories.stories if isinstance(s, StoryItem)]
    except Exception as e:
        log.warning("get_story_ids(%s): %s", username, e)
        return []