"""Native Telegram Sticker pack management, indexing, vision analysis, and sending."""

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Fallback paths
DEFAULT_STICKERS_DIR = Path.home() / ".hermes" / "stickers"
DEFAULT_INDEX_FILE = DEFAULT_STICKERS_DIR / "index.json"
DEFAULT_THUMBNAILS_DIR = DEFAULT_STICKERS_DIR / "thumbnails"

VISION_PROMPT = (
    "Describe this sticker in 1-2 concise sentences. Focus on what it depicts "
    "(character, action, emotion, facial expression). "
    "CRITICAL: If there is ANY text, caption, or subtitle visible on the sticker, "
    "quote the exact text verbatim in Russian/English on a new line starting with 'Text: \"...\"'. "
    "Then provide 5-8 mood/emotion tags in both Russian and English "
    "(e.g. laughing, смех, shock, шок, doge) as a comma-separated list on a new line "
    "starting with 'Tags:'."
)

BILINGUAL_SYNONYMS = {
    "смех": ["laugh", "laughing", "laughter", "funny", "amused", "joy", "hilarious", "lol"],
    "смешно": ["funny", "laugh", "hilarious", "amusing"],
    "ржу": ["laugh", "laughing", "lol", "rofl"],
    "шок": ["shock", "shocked", "shocking", "disbelief", "surprised", "gasp"],
    "удивление": ["surprised", "wonder", "shock"],
    "грусть": ["sad", "sadness", "crying", "unhappy", "sorrow"],
    "печаль": ["sad", "grief", "melancholy"],
    "плач": ["crying", "tears", "cry", "sob"],
    "слезы": ["tears", "crying", "sob"],
    "злость": ["angry", "anger", "mad", "rage", "furious"],
    "гнев": ["rage", "fury", "angry"],
    "палец": ["finger", "pointing", "thumbs"],
    "лайк": ["thumbs", "like", "approval", "positive"],
    "одобрение": ["approval", "thumbs up", "agree", "positive"],
    "альтман": ["altman", "sam altman"],
    "меллстрой": ["mellstroy", "melstroy"],
    "подозрение": ["suspicious", "skeptical", "doubt"],
    "скептицизм": ["skeptical", "skeptic"],
    "любовь": ["love", "heart", "crush", "romantic"],
    "мило": ["cute", "adorable", "sweet"],
    "пес": ["dog", "puppy", "pug", "shiba"],
    "собака": ["dog", "puppy", "shiba"],
    "кот": ["cat", "kitty", "kitten"],
    "кошка": ["cat", "kitty"],
    "сон": ["sleep", "sleeping", "tired", "sleepy"],
    "устал": ["tired", "exhausted", "sleepy"],
    "огонь": ["fire", "lit", "flame", "hot"],
    "рука": ["hand", "wave", "facepalm"],
    "фейспалм": ["facepalm"],
}



def extract_pack_name(input_str: str) -> str:
    """Extract clean pack name from name or Telegram URL."""
    s = (input_str or "").strip()
    if not s:
        return ""
    if "set=" in s:
        s = s.split("set=", 1)[1].split("&", 1)[0]
    elif "/" in s:
        s = s.rstrip("/").rsplit("/", 1)[-1]
    return s.strip()


def get_telegram_token() -> str:
    """Resolve Telegram bot token from env, ~/.hermes/.env, or config.yaml."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    token = v.strip().strip("'\"")
                    if token:
                        return token
        except Exception as e:
            logger.debug("Failed reading %s: %s", env_path, e)

    # Try loading from config.yaml
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        token = (
            cfg_get(cfg, "platforms", "telegram", "token")
            or cfg_get(cfg, "telegram", "token")
            or ""
        )
        if token:
            return str(token).strip()
    except Exception:
        pass

    return ""


def get_default_chat_id() -> str:
    """Resolve default chat ID from environment or ~/.hermes/.env."""
    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or os.environ.get("TELEGRAM_HOME_CHANNEL")
        or ""
    ).strip()
    if chat_id:
        return chat_id

    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k in ("TELEGRAM_HOME_CHANNEL", "TELEGRAM_CHAT_ID"):
                    val = v.strip().strip("'\"")
                    if val:
                        return val
        except Exception as e:
            logger.debug("Failed reading %s: %s", env_path, e)

    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        val = (
            cfg_get(cfg, "platforms", "telegram", "home_channel")
            or cfg_get(cfg, "platforms", "telegram", "chat_id")
            or cfg_get(cfg, "telegram", "home_channel")
            or ""
        )
        if val:
            return str(val).strip()
    except Exception:
        pass

    return ""


async def extract_video_keyframe(video_path: Path, output_image_path: Path) -> bool:
    """Extract a keyframe image from a video sticker (.webm) using ffmpeg."""
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    # Attempt extraction at 0.1s
    cmd1 = [
        "ffmpeg", "-y",
        "-ss", "00:00:00.1",
        "-i", str(video_path),
        "-vframes", "1",
        "-f", "image2",
        str(output_image_path),
    ]
    try:
        proc1 = await asyncio.create_subprocess_exec(
            *cmd1,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc1.wait()
        if output_image_path.exists() and output_image_path.stat().st_size > 0:
            return True
    except Exception as e:
        logger.debug("ffmpeg keyframe extraction with -ss failed: %s", e)

    # Fallback without -ss (for very short clips)
    cmd2 = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vframes", "1",
        "-f", "image2",
        str(output_image_path),
    ]
    try:
        proc2 = await asyncio.create_subprocess_exec(
            *cmd2,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc2.wait()
        return output_image_path.exists() and output_image_path.stat().st_size > 0
    except Exception as e:
        logger.warning("ffmpeg keyframe fallback extraction failed: %s", e)
        return False


def parse_vision_analysis(analysis_text: str, fallback_emoji: str = "") -> Tuple[str, List[str]]:
    """Parse description and mood_tags from vision LLM response."""
    if not analysis_text:
        desc = f"Sticker depicting {fallback_emoji}".strip() if fallback_emoji else "Sticker"
        tags = [fallback_emoji] if fallback_emoji else ["sticker"]
        return desc, tags

    lines = analysis_text.strip().splitlines()
    tags: List[str] = []
    desc_lines: List[str] = []

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower.startswith("tags:") or lower.startswith("mood tags:") or lower.startswith("keywords:"):
            tag_part = cleaned.split(":", 1)[1]
            extracted = [
                re.sub(r"^[#\s\-*]+|[#\s]+$", "", t).strip().lower()
                for t in tag_part.split(",")
            ]
            tags.extend([t for t in extracted if t])
        elif lower.startswith("text:") or lower.startswith("caption:") or lower.startswith("subtitle:"):
            caption_text = cleaned.split(":", 1)[1].strip().strip('"\'')
            if caption_text:
                desc_lines.append(f'Text: "{caption_text}"')
                # Добавляем слова из текста напрямую в теги для поиска
                caption_words = re.findall(r"\b[a-zA-Zа-яА-Я0-9]{2,}\b", caption_text.lower())
                tags.extend(caption_words)
        else:
            desc_lines.append(cleaned)

    description = " ".join(desc_lines).strip()
    if not description:
        description = f"Sticker depicting {fallback_emoji}".strip() if fallback_emoji else "Sticker"

    if not tags:
        # Generate simple fallback tags from description words + emoji
        words = re.findall(r"\b[a-zA-Zа-яА-Я0-9]{3,}\b", description.lower())
        stopwords = {
            "this", "that", "with", "from", "sticker", "shows", "depicts",
            "image", "illustration", "character", "icon", "background", "the",
            "and", "for", "are", "what", "then", "have", "been", "here",
            "этот", "стикер", "показывает", "изображен", "изображает", "персонаж",
        }
        filtered = [w for w in words if w not in stopwords][:5]
        if fallback_emoji:
            filtered.insert(0, fallback_emoji)
        tags = filtered or (["sticker"] if not fallback_emoji else [fallback_emoji])

    # Deduplicate while preserving order
    seen = set()
    deduped_tags: List[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            deduped_tags.append(t)

    return description, deduped_tags


def score_sticker(query: str, sticker: Dict[str, Any]) -> float:
    """Calculate relevance score between query and sticker metadata."""
    query_clean = query.lower().strip()
    if not query_clean:
        return 0.0

    raw_words = set(re.findall(r"[\w\d]+", query_clean))
    query_words = set(raw_words)

    # Expand query words with bilingual synonyms
    for w in raw_words:
        if w in BILINGUAL_SYNONYMS:
            query_words.update(BILINGUAL_SYNONYMS[w])

    score = 0.0


    emoji = (sticker.get("emoji") or "").strip()
    if emoji:
        if emoji in query:
            score += 25.0
        elif emoji == query_clean:
            score += 35.0

    tags = [str(t).lower().strip() for t in sticker.get("mood_tags", []) if t]
    for tag in tags:
        if tag == query_clean:
            score += 30.0
        elif tag in query_words:
            score += 15.0
        elif any(qw in tag for qw in query_words if len(qw) > 2):
            score += 8.0
        elif query_clean in tag or tag in query_clean:
            score += 10.0

    desc = (sticker.get("description") or "").lower()
    if desc:
        if query_clean in desc:
            score += 16.0
        desc_words = set(re.findall(r"[\w\d]+", desc))
        overlap = query_words & desc_words
        score += len(overlap) * 6.0
        for qw in query_words:
            if len(qw) > 2 and qw in desc:
                score += 3.0

    pack_name = (sticker.get("pack_name") or "").lower()
    pack_title = (sticker.get("pack_title") or "").lower()
    if pack_name and any(qw in pack_name for qw in query_words):
        score += 5.0
    if pack_title and any(qw in pack_title for qw in query_words):
        score += 5.0

    # Fuzzy match bonus against tags & description
    target_text = f"{' '.join(tags)} {desc} {emoji}".strip()
    if target_text:
        ratio = difflib.SequenceMatcher(None, query_clean, target_text[:200]).ratio()
        score += ratio * 10.0

    return score


def search_stickers(
    stickers: List[Dict[str, Any]],
    query: str,
    pack: Optional[str] = None,
    min_score: float = 1.0,
) -> List[Tuple[Dict[str, Any], float]]:
    """Search stickers matching query and optional pack filter, ranked by score."""
    candidates = stickers
    if pack:
        clean_pack = extract_pack_name(pack).lower()
        candidates = [
            s for s in stickers
            if s.get("pack_name", "").lower() == clean_pack
            or extract_pack_name(s.get("pack_name", "")).lower() == clean_pack
        ]

    scored: List[Tuple[Dict[str, Any], float]] = []
    for s in candidates:
        s_score = score_sticker(query, s)
        if s_score >= min_score:
            scored.append((s, s_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


class StickerStorage:
    """Manages sticker database in index.json."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_STICKERS_DIR
        self.index_file = self.base_dir / "index.json"
        self.thumbnails_dir = self.base_dir / "thumbnails"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        """Load index from disk."""
        if not self.index_file.exists():
            return {"version": 1, "packs": {}, "stickers": []}
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"version": 1, "packs": {}, "stickers": []}
            data.setdefault("version", 1)
            data.setdefault("packs", {})
            data.setdefault("stickers", [])
            return data
        except Exception as e:
            logger.warning("Failed reading stickers index at %s: %s", self.index_file, e)
            return {"version": 1, "packs": {}, "stickers": []}

    def save(self, data: Dict[str, Any]) -> None:
        """Atomically write index to disk."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="stickers_idx_", suffix=".json", dir=str(self.base_dir)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.index_file)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def get_sticker_by_unique_id(self, unique_id: str) -> Optional[Dict[str, Any]]:
        """Find existing sticker entry by file_unique_id."""
        index = self.load()
        for s in index.get("stickers", []):
            if s.get("file_unique_id") == unique_id:
                return s
        return None

    def upsert_pack_stickers(
        self,
        pack_name: str,
        pack_title: str,
        stickers: List[Dict[str, Any]],
        is_animated: bool = False,
        is_video: bool = False,
    ) -> None:
        """Upsert stickers and pack metadata in index."""
        index = self.load()
        existing_stickers = index.setdefault("stickers", [])
        existing_by_uid = {
            s.get("file_unique_id"): idx for idx, s in enumerate(existing_stickers)
            if s.get("file_unique_id")
        }

        all_pack_tags: Set[str] = set()

        for s in stickers:
            uid = s.get("file_unique_id")
            for t in s.get("mood_tags", []):
                all_pack_tags.add(t)

            if uid and uid in existing_by_uid:
                existing_stickers[existing_by_uid[uid]] = s
            else:
                existing_stickers.append(s)
                if uid:
                    existing_by_uid[uid] = len(existing_stickers) - 1

        # Count total indexed stickers for this pack
        pack_stickers = [
            s for s in existing_stickers
            if s.get("pack_name", "").lower() == pack_name.lower()
        ]

        packs = index.setdefault("packs", {})
        packs[pack_name] = {
            "name": pack_name,
            "title": pack_title,
            "count": len(pack_stickers),
            "is_animated": is_animated,
            "is_video": is_video,
            "indexed_at": time.time(),
            "sample_tags": sorted(list(all_pack_tags))[:10],
        }

        self.save(index)

        # Синхронизируем напрямую в нативный кэш стикеров Hermes,
        # чтобы входящие стикеры мгновенно распознавались без повторного вызова Vision
        try:
            from gateway.sticker_cache import cache_sticker_description
            for s in stickers:
                fuid = s.get("file_unique_id")
                desc = s.get("description")
                if fuid and desc:
                    cache_sticker_description(
                        file_unique_id=fuid,
                        description=desc,
                        emoji=s.get("emoji", ""),
                        set_name=pack_name
                    )
        except Exception:
            pass


class TelegramClient:
    """Telegram Bot API client."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or get_telegram_token()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{self.token}"

    def ensure_token(self) -> None:
        if not self.token:
            self.token = get_telegram_token()
            self.base_url = f"https://api.telegram.org/bot{self.token}"
            self.file_base_url = f"https://api.telegram.org/file/bot{self.token}"
        if not self.token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not configured in environment or ~/.hermes/.env."
            )

    async def get_sticker_set(self, pack_name: str) -> Dict[str, Any]:
        """Fetch sticker set details from Telegram API."""
        self.ensure_token()
        url = f"{self.base_url}/getStickerSet"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params={"name": pack_name})
            data = resp.json()
            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                raise RuntimeError(f"Telegram getStickerSet failed for '{pack_name}': {error_desc}")
            return data["result"]

    async def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """Get file path metadata for a file_id."""
        self.ensure_token()
        url = f"{self.base_url}/getFile"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params={"file_id": file_id})
            data = resp.json()
            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                raise RuntimeError(f"Telegram getFile failed for '{file_id}': {error_desc}")
            return data["result"]

    async def download_file(self, file_path: str, dest_path: Path) -> Path:
        """Download a file by its Telegram relative file_path."""
        self.ensure_token()
        url = f"{self.file_base_url}/{file_path}"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)
            return dest_path

    async def send_sticker(self, chat_id: Union[str, int], sticker_file_id: str) -> Dict[str, Any]:
        """Send a sticker to the given chat ID."""
        self.ensure_token()
        url = f"{self.base_url}/sendSticker"
        payload = {"chat_id": chat_id, "sticker": sticker_file_id}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                raise RuntimeError(f"Telegram sendSticker failed: {error_desc}")
            return data["result"]


class StickerService:
    """Core service orchestrating sticker pack indexing, vision analysis, search, and sending."""

    def __init__(
        self,
        storage: Optional[StickerStorage] = None,
        tg_client: Optional[TelegramClient] = None,
        vision_analyzer=None,
    ):
        self.storage = storage or StickerStorage()
        self.tg = tg_client or TelegramClient()
        self._vision_analyzer = vision_analyzer

    async def _analyze_image_with_vision(self, image_path: Path, emoji: str = "") -> Tuple[str, List[str]]:
        """Call Hermes vision_analyze_tool or provided vision analyzer."""
        if self._vision_analyzer:
            res = await self._vision_analyzer(str(image_path), VISION_PROMPT)
            if isinstance(res, str):
                try:
                    res_json = json.loads(res)
                    res_text = res_json.get("analysis", "") if res_json.get("success") else ""
                except Exception:
                    res_text = res
            elif isinstance(res, dict):
                res_text = res.get("analysis", "") if res.get("success") else ""
            else:
                res_text = str(res)
            return parse_vision_analysis(res_text, fallback_emoji=emoji)

        try:
            from tools.vision_tools import vision_analyze_tool
            res_str = await vision_analyze_tool(image_url=str(image_path), user_prompt=VISION_PROMPT)
            res_json = json.loads(res_str)
            if res_json.get("success"):
                analysis_text = res_json.get("analysis", "")
                return parse_vision_analysis(analysis_text, fallback_emoji=emoji)
            else:
                logger.warning("vision_analyze_tool returned success=false: %s", res_json.get("error"))
                return parse_vision_analysis("", fallback_emoji=emoji)
        except Exception as e:
            logger.warning("Error running vision_analyze_tool on %s: %s", image_path, e)
            return parse_vision_analysis("", fallback_emoji=emoji)

    async def index_pack(self, pack_input: str, limit: int = 50) -> Dict[str, Any]:
        """Index a sticker pack from Telegram."""
        pack_name = extract_pack_name(pack_input)
        if not pack_name:
            raise ValueError(f"Invalid sticker pack name or URL: '{pack_input}'")

        pack_info = await self.tg.get_sticker_set(pack_name)
        pack_title = pack_info.get("title", pack_name)
        is_animated = bool(pack_info.get("is_animated", False))
        is_video = bool(pack_info.get("is_video", False))
        stickers_raw = pack_info.get("stickers", [])

        total_stickers = len(stickers_raw)
        to_process = stickers_raw[: max(1, limit)]

        indexed_stickers: List[Dict[str, Any]] = []
        already_indexed_count = 0
        newly_indexed_count = 0

        # Optional sticker_cache warm-up helper
        cache_fn = None
        try:
            from gateway.sticker_cache import cache_sticker_description
            cache_fn = cache_sticker_description
        except Exception:
            pass

        for st in to_process:
            file_id = st.get("file_id")
            file_unique_id = st.get("file_unique_id")
            emoji = st.get("emoji", "")

            # Check if already indexed in our local storage
            existing = self.storage.get_sticker_by_unique_id(file_unique_id) if file_unique_id else None
            if existing and existing.get("description"):
                already_indexed_count += 1
                # Ensure current file_id is preserved
                existing["file_id"] = file_id
                indexed_stickers.append(existing)
                continue

            thumb_info = st.get("thumbnail") or st.get("thumb")
            thumb_file_id = thumb_info.get("file_id") if isinstance(thumb_info, dict) else None

            description = ""
            mood_tags: List[str] = []
            final_thumb_path: Optional[Path] = None

            # Determine format
            if is_video:
                format_type = "video"
            elif is_animated:
                format_type = "animated"
            else:
                format_type = "static"

            try:
                if format_type == "static":
                    # Static WebP: download directly and analyze
                    file_meta = await self.tg.get_file_info(file_id)
                    rel_path = file_meta.get("file_path", "")
                    ext = Path(rel_path).suffix or ".webp"
                    dest_file = self.storage.thumbnails_dir / f"{file_unique_id}{ext}"
                    await self.tg.download_file(rel_path, dest_file)
                    final_thumb_path = dest_file
                    description, mood_tags = await self._analyze_image_with_vision(dest_file, emoji=emoji)

                elif format_type == "video":
                    # Video WebM: download video, extract keyframe via ffmpeg, analyze
                    file_meta = await self.tg.get_file_info(file_id)
                    rel_path = file_meta.get("file_path", "")
                    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_vid:
                        tmp_vid_path = Path(tmp_vid.name)
                    try:
                        await self.tg.download_file(rel_path, tmp_vid_path)
                        keyframe_path = self.storage.thumbnails_dir / f"{file_unique_id}_frame.png"
                        ok = await extract_video_keyframe(tmp_vid_path, keyframe_path)
                        if ok:
                            final_thumb_path = keyframe_path
                            description, mood_tags = await self._analyze_image_with_vision(keyframe_path, emoji=emoji)
                        else:
                            # Fallback if ffmpeg failed
                            description, mood_tags = parse_vision_analysis("", fallback_emoji=emoji)
                    finally:
                        if tmp_vid_path.exists():
                            try:
                                tmp_vid_path.unlink()
                            except OSError:
                                pass

                elif format_type == "animated":
                    # Animated TGS: if static thumbnail exists, download and analyze
                    if thumb_file_id:
                        try:
                            thumb_meta = await self.tg.get_file_info(thumb_file_id)
                            rel_path = thumb_meta.get("file_path", "")
                            ext = Path(rel_path).suffix or ".webp"
                            dest_thumb = self.storage.thumbnails_dir / f"{file_unique_id}_thumb{ext}"
                            await self.tg.download_file(rel_path, dest_thumb)
                            final_thumb_path = dest_thumb
                            description, mood_tags = await self._analyze_image_with_vision(dest_thumb, emoji=emoji)
                        except Exception as e:
                            logger.debug("Failed downloading thumbnail for animated sticker: %s", e)
                            description, mood_tags = parse_vision_analysis("", fallback_emoji=emoji)
                    else:
                        description, mood_tags = parse_vision_analysis("", fallback_emoji=emoji)

            except Exception as e:
                logger.warning("Error indexing sticker %s in pack %s: %s", file_unique_id, pack_name, e)
                description, mood_tags = parse_vision_analysis("", fallback_emoji=emoji)

            entry = {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "emoji": emoji,
                "description": description,
                "mood_tags": mood_tags,
                "pack_name": pack_name,
                "pack_title": pack_title,
                "format": format_type,
                "thumbnail_path": str(final_thumb_path) if final_thumb_path else None,
                "indexed_at": time.time(),
            }
            indexed_stickers.append(entry)
            newly_indexed_count += 1

            # Populate Hermes sticker_cache if available
            if cache_fn and file_unique_id and description:
                try:
                    cache_fn(file_unique_id, description, emoji=emoji, set_name=pack_name)
                except Exception:
                    pass

        # Update index storage
        self.storage.upsert_pack_stickers(
            pack_name=pack_name,
            pack_title=pack_title,
            stickers=indexed_stickers,
            is_animated=is_animated,
            is_video=is_video,
        )

        return {
            "success": True,
            "pack_name": pack_name,
            "pack_title": pack_title,
            "indexed_count": newly_indexed_count,
            "already_indexed_count": already_indexed_count,
            "total_in_pack": total_stickers,
            "sample_stickers": [
                {
                    "file_id": s["file_id"],
                    "emoji": s.get("emoji", ""),
                    "description": s.get("description", ""),
                    "mood_tags": s.get("mood_tags", []),
                }
                for s in indexed_stickers[:5]
            ],
        }

    async def send_best_matching_sticker(
        self,
        query: str,
        pack: Optional[str] = None,
        chat_id: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """Find best matching sticker and send it via Telegram."""
        if not query or not query.strip():
            raise ValueError("Query string cannot be empty.")

        index = self.storage.load()
        stickers = index.get("stickers", [])
        if not stickers:
            raise RuntimeError(
                "No stickers have been indexed yet. Use 'telegram_add_stickers' first."
            )

        matches = search_stickers(stickers, query=query, pack=pack)
        if not matches:
            # Check if any stickers exist in that pack
            if pack:
                msg = f"No matching stickers found for query '{query}' in pack '{pack}'."
            else:
                msg = f"No matching stickers found for query '{query}' across indexed packs."
            raise RuntimeError(msg)

        best_sticker, best_score = matches[0]
        file_id = best_sticker.get("file_id")
        if not file_id:
            raise RuntimeError("Matched sticker is missing a valid file_id.")

        target_chat_id = str(chat_id).strip() if chat_id is not None and str(chat_id).strip() else get_default_chat_id()
        if not target_chat_id:
            raise ValueError(
                "Target chat_id is required. Provide 'chat_id' parameter or set "
                "TELEGRAM_HOME_CHANNEL in ~/.hermes/.env."
            )

        send_res = await self.tg.send_sticker(target_chat_id, file_id)
        message_id = send_res.get("message_id")

        return {
            "success": True,
            "chat_id": target_chat_id,
            "message_id": message_id,
            "score": round(best_score, 2),
            "matched": {
                "file_id": file_id,
                "file_unique_id": best_sticker.get("file_unique_id"),
                "emoji": best_sticker.get("emoji", ""),
                "description": best_sticker.get("description", ""),
                "mood_tags": best_sticker.get("mood_tags", []),
                "pack_name": best_sticker.get("pack_name", ""),
                "pack_title": best_sticker.get("pack_title", ""),
            },
        }

    def list_packs(self) -> Dict[str, Any]:
        """List all indexed packs with metrics and sample tags."""
        index = self.storage.load()
        packs = index.get("packs", {})
        stickers = index.get("stickers", [])

        pack_list: List[Dict[str, Any]] = []
        for name, p in packs.items():
            pack_stickers = [s for s in stickers if s.get("pack_name", "").lower() == name.lower()]
            emojis = list({s.get("emoji") for s in pack_stickers if s.get("emoji")})[:8]
            tags = p.get("sample_tags", [])
            if not tags:
                all_tags: Set[str] = set()
                for s in pack_stickers:
                    for t in s.get("mood_tags", []):
                        all_tags.add(t)
                tags = sorted(list(all_tags))[:10]

            pack_list.append({
                "name": name,
                "title": p.get("title", name),
                "count": p.get("count", len(pack_stickers)),
                "is_animated": p.get("is_animated", False),
                "is_video": p.get("is_video", False),
                "sample_tags": tags,
                "sample_emojis": emojis,
            })

        return {
            "success": True,
            "total_packs": len(pack_list),
            "total_stickers": len(stickers),
            "packs": pack_list,
        }


# =============================================================================
# Tool Schemas
# =============================================================================

TELEGRAM_ADD_STICKERS_SCHEMA = {
    "name": "telegram_add_stickers",
    "description": (
        "Index a Telegram sticker pack for searching and sending. Downloads stickers, "
        "analyzes static and animated/video stickers with vision (keyframe extraction via ffmpeg), "
        "extracts mood/action tags, and saves to the local sticker database."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pack_name": {
                "type": "string",
                "description": "Name or URL of the Telegram sticker pack (e.g. 'Animals', 'Sam_Altman', or 'https://t.me/addstickers/Animals').",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of stickers to index from the pack (default: 50).",
                "default": 50,
            },
        },
        "required": ["pack_name"],
    },
}

TELEGRAM_SEND_STICKER_SCHEMA = {
    "name": "telegram_send_sticker",
    "description": (
        "Search indexed stickers using keyword/semantic emotion matching and send the best "
        "matching sticker to a Telegram chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keywords, character name, emotion, or action (e.g. 'альтман палец', 'шок', 'смех', 'меллстрой', 'doge skeptical').",
            },
            "pack": {
                "type": "string",
                "description": "Optional sticker pack name to filter the search.",
            },
            "chat_id": {
                "type": "string",
                "description": "Optional Telegram chat ID. Defaults to current chat or TELEGRAM_HOME_CHANNEL.",
            },
        },
        "required": ["query"],
    },
}

TELEGRAM_LIST_STICKER_PACKS_SCHEMA = {
    "name": "telegram_list_sticker_packs",
    "description": "List all indexed Telegram sticker packs, sticker counts, and sample emotion/mood tags.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


# =============================================================================
# Tool Handlers
# =============================================================================

async def telegram_add_stickers_handler(args: Dict[str, Any], **kwargs) -> str:
    """Handler for telegram_add_stickers."""
    pack_name = args.get("pack_name", "")
    limit = int(args.get("limit", 50))
    service = StickerService()
    try:
        res = await service.index_pack(pack_name, limit=limit)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False, indent=2)


async def telegram_send_sticker_handler(args: Dict[str, Any], **kwargs) -> str:
    """Handler for telegram_send_sticker."""
    query = args.get("query", "")
    pack = args.get("pack")
    chat_id = args.get("chat_id")
    service = StickerService()
    try:
        res = await service.send_best_matching_sticker(query=query, pack=pack, chat_id=chat_id)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False, indent=2)


async def telegram_list_sticker_packs_handler(args: Dict[str, Any], **kwargs) -> str:
    """Handler for telegram_list_sticker_packs."""
    service = StickerService()
    try:
        res = service.list_packs()
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False, indent=2)
