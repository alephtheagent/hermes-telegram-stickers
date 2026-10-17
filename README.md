# Telegram Stickers Plugin for Hermes Agent

Native Hermes Agent plugin for Telegram sticker indexing, vision analysis (static WebP, animated TGS, and video WebM via Gemini Flash / ffmpeg), and automated sticker sending via tool calling.

## Features

- **Automated Pack Indexing (`telegram_add_stickers`)**:
  - Fetches sticker sets from Telegram Bot API.
  - Multimodal vision analysis using Hermes's configured vision model (`video_analyze_tool` and `vision_analyze_tool`).
  - Automatic subtitle and caption extraction (OCR) from video memes/stickers directly into search keywords.
  - Dynamic emotion/mood tag extraction in both Russian and English.
- **Semantic / Fuzzy Search & Sending (`telegram_send_sticker`)**:
  - High-precision matching by query, quotes, Russian text, emotions, character names, and emoji.
  - Sends stickers directly to chat or default platform channel.
- **Pack Telemetry (`telegram_list_sticker_packs`)**:
  - Fast overview of indexed packs and sample emotion tags.
- **Instant Cache Sync**:
  - Direct integration with Hermes's native `sticker_cache.json` for zero-latency (<1ms) incoming sticker recognition without repeated LLM/Vision calls.

## Tools Registered

- `telegram_add_stickers(pack_name, limit=50)`
- `telegram_send_sticker(query, pack=None, chat_id=None)`
- `telegram_list_sticker_packs()`

## Installation

Clone or copy into your Hermes plugins directory:

```bash
mkdir -p ~/.hermes/plugins/telegram-stickers
cp -r * ~/.hermes/plugins/telegram-stickers/
```

Enable via Hermes CLI:

```bash
hermes plugins enable telegram-stickers
```

Ensure `TELEGRAM_BOT_TOKEN` is present in your environment or `~/.hermes/.env`.
