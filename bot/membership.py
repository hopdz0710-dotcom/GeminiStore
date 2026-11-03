from __future__ import annotations

import logging

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.community import CHANNEL_CHAT_ID, CHANNEL_URL, GROUP_CHAT_ID, GROUP_URL
from bot.config import Settings
from bot.db import Database
from bot.i18n import tr

log = logging.getLogger(__name__)


def membership_keyboard(language: str) -> InlineKeyboardMarkup:
    if language == "en":
        labels = ("Join the official channel", "Join the official group", "Check membership")
    else:
        labels = ("الاشتراك في القناة الرسمية", "الانضمام إلى المجموعة الرسمية", "التحقق من الاشتراك")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[0], url=CHANNEL_URL)],
            [InlineKeyboardButton(text=labels[1], url=GROUP_URL)],
            [InlineKeyboardButton(text=labels[2], callback_data="u:membership_check")],
        ]
    )


async def required_memberships(bot: Bot, telegram_id: int) -> tuple[bool, bool]:
    results = []
    for chat_id in (CHANNEL_CHAT_ID, GROUP_CHAT_ID):
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=telegram_id)
            results.append(
                member.status in {"creator", "administrator", "member"}
                or (member.status == "restricted" and bool(getattr(member, "is_member", False)))
            )
        except Exception:
            log.exception("Could not verify required membership for chat %s.", chat_id)
            results.append(False)
    return results[0], results[1]


async def send_membership_prompt(
    event: Message | CallbackQuery,
    bot: Bot,
    db: Database,
    telegram_id: int,
    language: str | None = None,
) -> None:
    if language is None:
        user = await db.get_user(telegram_id)
        language = user["language"] if user else "ar"
    channel_member, group_member = await required_memberships(bot, telegram_id)
    text = (
        tr(language, "membership_verified")
        if channel_member and group_member
        else tr(language, "membership_required")
    )
    markup = membership_keyboard(language)
    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


class MembershipGateMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        settings: Settings = data["settings"]
        if user.id in settings.admin_ids:
            return await handler(event, data)

        if isinstance(event, Message):
            command = (event.text or "").split(maxsplit=1)[0].split("@", 1)[0]
            if command == "/start":
                return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "u:membership_check":
            return await handler(event, data)

        bot: Bot = data["bot"]
        channel_member, group_member = await required_memberships(bot, user.id)
        if channel_member and group_member:
            return await handler(event, data)

        db: Database = data["db"]
        user_record = await db.get_user(user.id)
        language = user_record["language"] if user_record else "ar"
        markup = membership_keyboard(language)
        text = tr(language, "membership_required")
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text, reply_markup=markup)
        elif isinstance(event, Message):
            await event.answer(text, reply_markup=markup)
        return None
