"""Telegram Stickers plugin for Hermes."""

if __package__:
    from .stickers import (
        TELEGRAM_ADD_STICKERS_SCHEMA,
        TELEGRAM_SEND_STICKER_SCHEMA,
        TELEGRAM_LIST_STICKER_PACKS_SCHEMA,
        telegram_add_stickers_handler,
        telegram_send_sticker_handler,
        telegram_list_sticker_packs_handler,
        StickerService,
        StickerStorage,
        TelegramClient,
    )
else:
    from stickers import (
        TELEGRAM_ADD_STICKERS_SCHEMA,
        TELEGRAM_SEND_STICKER_SCHEMA,
        TELEGRAM_LIST_STICKER_PACKS_SCHEMA,
        telegram_add_stickers_handler,
        telegram_send_sticker_handler,
        telegram_list_sticker_packs_handler,
        StickerService,
        StickerStorage,
        TelegramClient,
    )


def register(ctx):
    """Register telegram sticker tools with Hermes."""
    ctx.register_tool(
        name="telegram_add_stickers",
        toolset="telegram_stickers",
        schema=TELEGRAM_ADD_STICKERS_SCHEMA,
        handler=telegram_add_stickers_handler,
        is_async=True,
        description="Index a Telegram sticker pack with vision analysis",
        emoji="📦",
    )

    ctx.register_tool(
        name="telegram_send_sticker",
        toolset="telegram_stickers",
        schema=TELEGRAM_SEND_STICKER_SCHEMA,
        handler=telegram_send_sticker_handler,
        is_async=True,
        description="Search and send matching sticker to Telegram chat",
        emoji="🏷️",
    )

    ctx.register_tool(
        name="telegram_list_sticker_packs",
        toolset="telegram_stickers",
        schema=TELEGRAM_LIST_STICKER_PACKS_SCHEMA,
        handler=telegram_list_sticker_packs_handler,
        is_async=True,
        description="List all indexed Telegram sticker packs",
        emoji="📋",
    )


__all__ = [
    "register",
    "StickerService",
    "StickerStorage",
    "TelegramClient",
    "TELEGRAM_ADD_STICKERS_SCHEMA",
    "TELEGRAM_SEND_STICKER_SCHEMA",
    "TELEGRAM_LIST_STICKER_PACKS_SCHEMA",
    "telegram_add_stickers_handler",
    "telegram_send_sticker_handler",
    "telegram_list_sticker_packs_handler",
]
