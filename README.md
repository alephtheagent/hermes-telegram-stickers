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

## Usage & Agent Interaction

The plugin registers native Hermes tools. You don't need any special CLI commands — just talk to your Hermes Agent naturally:

### 1. Adding & Indexing Packs
Tell the agent to add a sticker pack by name or Telegram link:
- *"Добавь стикерпак https://t.me/addstickers/Animals"*
- *"Заиндексируй пак Sam_Altman"*
- *"Add sticker pack Animals with limit 30"*

Under the hood, the agent automatically executes:
```json
telegram_add_stickers(pack_name="Animals", limit=50)
```
The agent downloads each sticker, analyzes it with the active vision model (extracting keyframes from video/animated stickers), transcribes visible captions/subtitles, tags emotions, and saves it to `~/.hermes/stickers/index.json` while syncing directly to `sticker_cache.json`.

### 2. Sending Stickers
The agent autonomously decides when to send a sticker, or you can ask directly:
- *"Кинь стикер с Альтманом где он одобряет"*
- *"Ответь стикером с Меллстроем"*
- *"Send a funny doge sticker"*

The agent invokes:
```json
telegram_send_sticker(query="альтман одобрение", pack="Sam_Altman")
```
It matches based on OCR text, character names, emotions, and emoji, then delivers the sticker natively to the current Telegram chat.

### 3. Listing Packs
Ask the agent what packs are available:
- *"Какие стикерпаки у тебя есть?"* -> agent runs `telegram_list_sticker_packs()`.

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
