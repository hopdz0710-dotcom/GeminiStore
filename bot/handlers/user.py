from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Filter
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from asyncpg import UniqueViolationError

from bot.config import Settings
from bot.db import Database, StoreError, money
from bot.i18n import tr
from bot.keyboards import inline, main_menu
from bot.membership import (
    membership_keyboard,
    required_memberships,
    send_membership_prompt,
)
from bot.order_messages import purchase_confirmation

log = logging.getLogger(__name__)
router = Router(name="user")

DURATIONS = (1, 3, 6, 12)
TOPUP_AMOUNTS = (500, 1000, 2000, 5000)


class HasUserSession(Filter):
    async def __call__(self, message: Message, db: Database) -> bool:
        return bool(message.from_user and await db.get_session(message.from_user.id, "user"))


async def language_for(db: Database, telegram_id: int) -> str:
    user = await db.get_user(telegram_id)
    return user["language"] if user else "ar"


async def notify_admins(bot: Bot, admin_ids: frozenset[int], text: str, markup=None) -> None:
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception:
            log.info("Admin notification deferred; admin chat may not be open.")


def amount_text(amount: Decimal, currency: str) -> str:
    return f"{amount.normalize():f}" if currency == "USD" else f"{amount.quantize(Decimal('1')):f}"


def format_date(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"


@router.message(CommandStart())
async def start(message: Message, bot: Bot, db: Database) -> None:
    if not message.from_user:
        return
    payload = (message.text or "").split(maxsplit=1)
    referrer_id = None
    if len(payload) == 2:
        match = re.fullmatch(r"ref_([1-9][0-9]{0,18})", payload[1].strip())
        if match:
            referrer_id = int(match.group(1))
    language, created = await db.register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name or "",
        referrer_id,
    )
    channel_member, group_member = await required_memberships(bot, message.from_user.id)
    if not (channel_member and group_member):
        await send_membership_prompt(message, bot, db, message.from_user.id, language)
        return
    text = tr(language, "welcome")
    if created:
        text += "\n\n" + tr(language, "reward")
    await message.answer(text, reply_markup=main_menu(language))


@router.message(Command("cancel"))
async def cancel_user_action(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    await db.clear_session(message.from_user.id, "user")
    await db.clear_session(message.from_user.id, "admin")
    language = await language_for(db, message.from_user.id)
    await message.answer(tr(language, "cancelled"), reply_markup=main_menu(language))


@router.message(F.text.in_({"🛍️ المتجر", "🛍️ Store", "🛍 المتجر", "🛍 Store"}))
async def store_button(message: Message, db: Database) -> None:
    if message.from_user:
        await show_store(message, db, message.from_user.id)


@router.message(F.text.in_({"💰 شحن رصيدي", "💰 Top up balance"}))
async def topup_button(message: Message, db: Database) -> None:
    if message.from_user:
        await show_wallet_and_topup(message, db, message.from_user.id)


@router.message(F.text.in_({"📦 طلباتي", "📦 My orders"}))
async def orders_button(message: Message, db: Database) -> None:
    if message.from_user:
        await show_orders(message, db, message.from_user.id)


@router.message(F.text.in_({"🎧 الدعم الفني", "🎧 Support"}))
async def support_button(message: Message, db: Database) -> None:
    if message.from_user:
        await begin_support(message, db, message.from_user.id)


@router.message(F.text.in_({"⚙️ الإعدادات", "⚙️ Settings"}))
async def settings_button(message: Message, db: Database) -> None:
    if message.from_user:
        await show_settings(message, db, message.from_user.id)


@router.message(F.text.in_({"👥 دعوة الأصدقاء", "👥 Invite friends"}))
async def referral_button(message: Message, bot: Bot, db: Database) -> None:
    if not message.from_user:
        return
    language = await language_for(db, message.from_user.id)
    bot_user = await bot.get_me()
    if not bot_user.username:
        await message.answer(tr(language, "generic_error"))
        return
    summary = await db.referral_summary(message.from_user.id)
    link = f"https://t.me/{bot_user.username}?start=ref_{message.from_user.id}"
    await message.answer(
        tr(
            language,
            "referral_summary",
            link=escape(link),
            referrals=summary["referrals"],
            rewarded=summary["rewarded_referrals"],
            earned=amount_text(money(summary["earned_dzd"]), "DZD"),
        ),
        disable_web_page_preview=True,
    )


@router.message(F.text.in_({"🎁 إرسال هدية لصديق", "🎁 Send a gift"}))
async def gift_button(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    language = await language_for(db, message.from_user.id)
    await db.set_session(message.from_user.id, "user", "gift_details", {})
    await message.answer(
        tr(language, "gift_prompt"),
        reply_markup=inline([[(tr(language, "cancel"), "u:cancel")]]),
    )


async def show_store(message: Message, db: Database, telegram_id: int) -> None:
    language = await language_for(db, telegram_id)
    if await db.setting("maintenance") == "true":
        await message.answer(tr(language, "maintenance"), reply_markup=main_menu(language))
        return
    products = await db.list_products(active_only=False)
    if not products:
        await message.answer(tr(language, "store_title") + "\n—", reply_markup=main_menu(language))
        return
    await message.answer(
        tr(language, "store_title"),
        reply_markup=inline([[
            (
                row["name"] if row["active"] else row["name"] + tr(language, "unavailable_badge"),
                f"u:product:{row['id']}",
            )
        ] for row in products]),
    )


async def show_wallet_and_topup(message: Message, db: Database, telegram_id: int) -> None:
    language = await language_for(db, telegram_id)
    if await db.setting("maintenance") == "true":
        await message.answer(tr(language, "maintenance"), reply_markup=main_menu(language))
        return
    balances = await db.wallet_balances(telegram_id)
    text = tr(
        language,
        "wallet",
        dzd=amount_text(balances.get("DZD", Decimal(0)), "DZD"),
        usd=amount_text(balances.get("USD", Decimal(0)), "USD"),
    )
    await message.answer(text, reply_markup=inline([
        [(tr(language, "topup_currency", currency="DZD"), "u:topup_currency:DZD"),
         (tr(language, "topup_currency", currency="USD"), "u:topup_currency:USD")],
    ]))


async def show_orders(message: Message, db: Database, telegram_id: int) -> None:
    language = await language_for(db, telegram_id)
    orders = await db.user_orders(telegram_id)
    if not orders:
        await message.answer(tr(language, "orders_empty"), reply_markup=main_menu(language))
        return
    lines = [tr(language, "orders_title")]
    for order in orders:
        status = tr(language, f"status_{order['status']}")
        subscription = ""
        if order["joined_at"]:
            subscription = tr(
                language,
                "subscription_dates",
                start=format_date(order["joined_at"]),
                end=format_date(order["expires_at"]),
            )
        lines.append(
            tr(
                language,
                "order_line",
                code=escape(order["order_code"]),
                product=escape(order["product_name"]),
                months=order["duration_months"],
                price=amount_text(money(order["price"]), order["currency"]),
                currency=order["currency"],
                status=status,
                created=format_date(order["created_at"]),
                subscription=subscription,
            )
        )
    await message.answer("\n\n".join(lines), reply_markup=main_menu(language))


async def show_settings(message: Message, db: Database, telegram_id: int) -> None:
    language = await language_for(db, telegram_id)
    await message.answer(
        tr(language, "settings_title"),
        reply_markup=inline([[
            ("العربية", "u:language:ar"),
            ("English", "u:language:en"),
        ]]),
    )


async def begin_support(message: Message, db: Database, telegram_id: int) -> None:
    language = await language_for(db, telegram_id)
    ticket = await db.create_support_ticket(telegram_id)
    await db.set_session(telegram_id, "user", "support", {"ticket_id": ticket["id"]})
    await message.answer(
        tr(language, "support_prompt"),
        reply_markup=inline([[(tr(language, "cancel"), "u:support:stop")]]),
    )


@router.callback_query(F.data.startswith("u:"))
async def user_callback(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    telegram_id = callback.from_user.id
    language = await language_for(db, telegram_id)
    parts = (callback.data or "").split(":")
    try:
        action = parts[1]
        if action == "membership_check":
            channel_member, group_member = await required_memberships(bot, telegram_id)
            if channel_member and group_member:
                await callback.message.answer(
                    tr(language, "membership_verified"),
                    reply_markup=main_menu(language),
                )
            else:
                await callback.message.answer(
                    tr(language, "membership_required"),
                    reply_markup=membership_keyboard(language),
                )
        elif action == "shop":
            products = await db.list_products(active_only=False)
            await callback.message.answer(
                tr(language, "store_title"),
                reply_markup=inline([[
                    (
                        p["name"] if p["active"] else p["name"] + tr(language, "unavailable_badge"),
                        f"u:product:{p['id']}",
                    )
                ] for p in products]),
            )
        elif action == "product":
            if await db.setting("maintenance") == "true":
                await callback.message.answer(tr(language, "maintenance"))
            else:
                product_id = int(parts[2])
                product = await db.get_product(product_id)
                if not product:
                    raise StoreError("unavailable")
                if not product["active"]:
                    prices = await db.get_prices(product_id)
                    lookup = {
                        (price["duration_months"], price["currency"]): price
                        for price in prices
                    }
                    price_lines = []
                    for months in DURATIONS:
                        values = []
                        for currency in ("DZD", "USD"):
                            price = lookup.get((months, currency))
                            if price and price["configured"] and price["amount"] > 0:
                                values.append(
                                    f"{amount_text(money(price['amount']), currency)} {currency}"
                                )
                        if values:
                            price_lines.append(
                                f"{months} {tr(language, 'month_unit')}: {', '.join(values)}"
                            )
                    await callback.message.answer(
                        f"<b>{escape(product['name'])}</b>\n{tr(language, 'product_unavailable')}\n"
                        f"{tr(language, 'catalog_price_title')}\n" + ("\n".join(price_lines) or "—")
                    )
                else:
                    rows = [[(f"{months} " + tr(language, "month_unit"),
                              f"u:duration:{product_id}:{months}")] for months in DURATIONS]
                    await callback.message.answer(
                        f"<b>{escape(product['name'])}</b>\n{tr(language, 'duration_title')}",
                        reply_markup=inline(rows),
                    )
        elif action == "duration":
            product_id, months = int(parts[2]), int(parts[3])
            product = await db.get_product(product_id)
            prices = await db.available_prices(product_id, months)
            if not product or not prices:
                raise StoreError("unavailable")
            currency_buttons = [
                (f"{amount_text(money(row['amount']), row['currency'])} {row['currency']}",
                 f"u:checkout:{product_id}:{months}:{row['currency']}")
                for row in prices
            ]
            await callback.message.answer(tr(language, "currency_title"),
                                          reply_markup=inline([currency_buttons]))
        elif action == "checkout":
            product_id, months, currency = int(parts[2]), int(parts[3]), parts[4]
            draft = await db.create_checkout_draft(telegram_id, product_id, months, currency)
            text = tr(
                language,
                "checkout",
                product=escape(draft["product_name"]),
                months=months,
                price=amount_text(money(draft["price"]), currency),
                currency=currency,
            )
            await callback.message.answer(
                text,
                reply_markup=inline([[
                    (tr(language, "confirm"), f"u:confirm:{draft['id']}"),
                    (tr(language, "cancel"), "u:cancel"),
                ], [("🎟️ استخدام كوبون" if language == "ar" else "🎟️ Use a coupon",
                     f"u:coupon:{draft['id']}")]),
            )
        elif action == "coupon":
            draft_id = uuid.UUID(parts[2])
            await db.set_session(
                telegram_id, "user", "coupon_code", {"draft_id": str(draft_id)}
            )
            await callback.message.answer(
                tr(language, "coupon_prompt"),
                reply_markup=inline([[(tr(language, "cancel"), "u:cancel")]]),
            )
        elif action == "coupon_remove":
            draft_id = uuid.UUID(parts[2])
            if not await db.remove_coupon_from_draft(telegram_id, draft_id):
                raise StoreError("draft_not_open")
            draft = await db.pool.fetchrow(
                """
                SELECT d.*, p.name AS product_name
                FROM checkout_drafts d JOIN products p ON p.id = d.product_id
                WHERE d.id = $1 AND d.user_id = $2
                """,
                draft_id,
                telegram_id,
            )
            if not draft:
                raise StoreError("draft_not_open")
            await callback.message.answer(
                tr(
                    language,
                    "checkout",
                    product=escape(draft["product_name"]),
                    months=draft["duration_months"],
                    price=amount_text(money(draft["price"]), draft["currency"]),
                    currency=draft["currency"],
                ),
                reply_markup=inline([
                    [(tr(language, "confirm"), f"u:confirm:{draft_id}"),
                     (tr(language, "cancel"), "u:cancel")],
                    [("🎟️ استخدام كوبون" if language == "ar" else "🎟️ Use a coupon",
                      f"u:coupon:{draft_id}")],
                ]),
            )
        elif action == "confirm":
            draft_id = uuid.UUID(parts[2])
            result, payload = await db.confirm_checkout(telegram_id, draft_id)
            if result == "insufficient":
                await callback.message.answer(
                    tr(
                        language,
                        "not_enough",
                        shortage=amount_text(payload["shortage"], payload["currency"]),
                        currency=payload["currency"],
                    ),
                    reply_markup=inline([[(tr(language, "topup_button"), "u:topup")]]),
                )
            else:
                order = payload
                order_detail = await db.order_detail(order["id"])
                if result == "duplicate":
                    text = tr(language, "purchase_duplicate", code=escape(order["order_code"]))
                else:
                    text = purchase_confirmation(order_detail, language)
                await callback.message.answer(text)
                if result == "created":
                    claimed = await db.claim_order_notification(order["id"], "purchase")
                    if claimed:
                        await db.mark_order_notification(order["id"], "purchase", True)
                    admin_text = (
                        f"🛒 <b>طلب شراء {escape(order['order_code'])}</b>\n"
                        f"المعرّف: <code>{telegram_id}</code>\n"
                        f"المستخدم: {escape(order_detail['first_name'] or '')} "
                        f"@{escape(order_detail['username'] or '—')}\n"
                        f"المنتج: {escape(order_detail['product_name'])} — {order['duration_months']} شهر\n"
                        f"السعر: {amount_text(money(order['price']), order['currency'])} {order['currency']}\n"
                        f"التاريخ: {format_date(order['created_at'])}"
                    )
                    await notify_admins(
                        bot,
                        settings.admin_ids,
                        admin_text,
                        inline([("فتح الطلب", f"a:order:{order['id']}")]),
                    )
        elif action == "gift_confirm":
            draft_id = uuid.UUID(parts[2])
            result, transfer = await db.confirm_gift(draft_id, telegram_id)
            if result == "insufficient":
                await callback.message.answer(
                    tr(
                        language,
                        "gift_insufficient",
                        shortage=amount_text(transfer["shortage"], "DZD"),
                    )
                )
            elif result == "duplicate":
                await callback.message.answer(tr(language, "gift_duplicate"))
            else:
                sender = await db.get_user(transfer["sender_id"])
                recipient = await db.get_user(transfer["receiver_id"])
                sender_name = escape(
                    f"@{recipient['username']}" if recipient and recipient["username"]
                    else recipient["first_name"] if recipient else "—"
                )
                recipient_name = escape(
                    f"@{sender['username']}" if sender and sender["username"]
                    else sender["first_name"] if sender else "—"
                )
                amount_text_value = amount_text(money(transfer["amount"]), "DZD")
                if sender:
                    try:
                        await bot.send_message(
                            transfer["sender_id"],
                            tr(
                                sender["language"],
                                "gift_sender_notice",
                                amount=amount_text_value,
                                recipient=sender_name,
                                code=escape(transfer["gift_code"]),
                            ),
                            reply_markup=main_menu(sender["language"]),
                        )
                    except Exception:
                        log.exception("Gift completed but sender notification failed.")
                if recipient:
                    try:
                        await bot.send_message(
                            transfer["receiver_id"],
                            tr(
                                recipient["language"],
                                "gift_receiver_notice",
                                amount=amount_text_value,
                                sender=recipient_name,
                                code=escape(transfer["gift_code"]),
                            ),
                            reply_markup=main_menu(recipient["language"]),
                        )
                    except Exception:
                        log.exception("Gift completed but receiver notification failed.")
                await callback.message.answer(
                    tr(language, "gift_complete", code=escape(transfer["gift_code"]))
                )
        elif action == "gift_cancel":
            draft_id = uuid.UUID(parts[2])
            await db.cancel_gift_draft(draft_id, telegram_id)
            await callback.message.answer(tr(language, "cancelled"), reply_markup=main_menu(language))
        elif action == "cancel":
            await db.clear_session(telegram_id, "user")
            await callback.message.answer(tr(language, "cancelled"))
        elif action == "topup":
            await show_wallet_and_topup(callback.message, db, telegram_id)
        elif action == "topup_currency":
            currency = parts[2]
            if await db.setting("maintenance") == "true":
                await callback.message.answer(tr(language, "maintenance"))
            elif currency == "USD":
                values = [
                    await db.setting(key)
                    for key in ("binance_address", "binance_network", "binance_currency", "binance_instructions")
                ]
                if not all(values[:3]):
                    await callback.message.answer(tr(language, "usd_unavailable"))
                else:
                    await db.set_session(telegram_id, "user", "topup_amount_usd", {})
                    await callback.message.answer(tr(language, "enter_amount"))
            else:
                await callback.message.answer(
                    tr(language, "choose_amount"),
                    reply_markup=inline(
                        [[(f"{amount} DZD", f"u:amount:DZD:{amount}") for amount in TOPUP_AMOUNTS[:2]],
                         [(f"{amount} DZD", f"u:amount:DZD:{amount}") for amount in TOPUP_AMOUNTS[2:]],
                         [(tr(language, "custom_amount"), "u:custom_amount:DZD")]]
                    ),
                )
        elif action == "amount":
            currency, amount = parts[2], Decimal(parts[3])
            await prepare_topup(callback.message, db, telegram_id, currency, amount, language)
        elif action == "custom_amount":
            currency = parts[2]
            state = "topup_amount_dzd" if currency == "DZD" else "topup_amount_usd"
            await db.set_session(telegram_id, "user", state, {})
            await callback.message.answer(tr(language, "enter_amount"))
        elif action == "language":
            locale = parts[2]
            await db.set_language(telegram_id, locale)
            await callback.message.answer(tr(locale, "language_saved"), reply_markup=main_menu(locale))
        elif action == "support" and parts[2] == "stop":
            await db.clear_session(telegram_id, "user")
            await callback.message.answer(tr(language, "support_closed"), reply_markup=main_menu(language))
    except (ValueError, IndexError, InvalidOperation, StoreError) as exc:
        if isinstance(exc, StoreError) and str(exc) == "maintenance":
            await callback.message.answer(tr(language, "maintenance"))
        elif isinstance(exc, StoreError) and str(exc) == "coupon_exhausted":
            await callback.message.answer(tr(language, "coupon_exhausted"))
        elif isinstance(exc, StoreError) and str(exc) == "coupon_currency_mismatch":
            await callback.message.answer(tr(language, "coupon_currency_mismatch"))
        elif isinstance(exc, StoreError) and str(exc) in {"coupon_unavailable", "draft_not_open"}:
            await callback.message.answer(tr(language, "coupon_invalid"))
        elif isinstance(exc, StoreError) and str(exc) == "gift_draft_closed":
            await callback.message.answer(tr(language, "gift_duplicate"))
        else:
            await callback.message.answer(tr(language, "generic_error"))
    except Exception:
        log.exception("User callback failed.")
        await callback.message.answer(tr(language, "generic_error"))
    finally:
        await callback.answer()


async def prepare_topup(
    message: Message,
    db: Database,
    telegram_id: int,
    currency: str,
    amount: Decimal,
    language: str,
) -> None:
    if await db.setting("maintenance") == "true":
        await message.answer(tr(language, "maintenance"))
        return
    value = money(amount)
    if not value.is_finite() or value <= 0 or (currency == "DZD" and value != value.quantize(Decimal("1"))):
        await message.answer(tr(language, "invalid_amount"))
        return
    if currency == "DZD":
        await db.set_session(
            telegram_id,
            "user",
            "topup_dzd_receipt",
            {"currency": currency, "amount": str(value)},
        )
        await message.answer(
            tr(language, "dzd_instructions", amount=amount_text(value, currency)),
            reply_markup=inline([[(tr(language, "cancel"), "u:cancel")]]),
        )
        return
    if currency != "USD":
        await message.answer(tr(language, "invalid_amount"))
        return
    address, network, coin, instructions = [
        await db.setting(key)
        for key in ("binance_address", "binance_network", "binance_currency", "binance_instructions")
    ]
    if not address or not network or not coin:
        await message.answer(tr(language, "usd_unavailable"))
        return
    await db.set_session(
        telegram_id,
        "user",
        "topup_usd_proof",
        {"currency": "USD", "amount": str(value)},
    )
    await message.answer(
        tr(
            language,
            "usd_instructions",
            amount=amount_text(value, currency),
            address=escape(address),
            network=escape(network),
            coin=escape(coin),
            instructions=escape(instructions or "—"),
        ),
        reply_markup=inline([[(tr(language, "cancel"), "u:cancel")]]),
    )


async def save_topup_submission(
    message: Message,
    bot: Bot,
    db: Database,
    settings: Settings,
    session: tuple[str, dict],
    receipt_file_id: str | None = None,
    txid: str | None = None,
) -> None:
    if not message.from_user:
        return
    state, payload = session
    language = await language_for(db, message.from_user.id)
    try:
        request = await db.create_topup(
            message.from_user.id,
            payload["currency"],
            Decimal(payload["amount"]),
            receipt_file_id=receipt_file_id,
            txid=txid,
        )
        await db.clear_session(message.from_user.id, "user")
        await message.answer(tr(language, "topup_created", code=escape(request["request_code"])))
        user = await db.get_user(message.from_user.id)
        admin_text = (
            f"💳 <b>طلب شحن {escape(request['request_code'])}</b>\n"
            f"المعرّف: <code>{message.from_user.id}</code>\n"
            f"المستخدم: {escape(user['first_name'] or '')} @{escape(user['username'] or '—')}\n"
            f"المبلغ: {amount_text(money(request['amount']), request['currency'])} {request['currency']}\n"
            f"TXID: <code>{escape(txid or '—')}</code>"
        )
        markup = inline([[("فتح طلب الشحن", f"a:topup:{request['id']}")]])
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, admin_text, reply_markup=markup)
                if receipt_file_id:
                    await bot.send_photo(admin_id, receipt_file_id, caption=request["request_code"])
            except Exception:
                log.info("Top-up saved; admin delivery will need to be viewed in the panel.")
    except UniqueViolationError:
        await message.answer(tr(language, "duplicate_proof"))
    except Exception:
        log.exception("Could not save top-up request.")
        await message.answer(tr(language, "generic_error"))


@router.message(F.photo, HasUserSession())
async def user_photo(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    if not message.from_user:
        return
    session = await db.get_session(message.from_user.id, "user")
    if not session:
        return
    state, payload = session
    photo_id = message.photo[-1].file_id
    if state == "topup_dzd_receipt":
        await save_topup_submission(message, bot, db, settings, session, receipt_file_id=photo_id)
    elif state == "topup_usd_proof":
        await save_topup_submission(message, bot, db, settings, session, receipt_file_id=photo_id)
    elif state == "support":
        await save_support_message(message, bot, db, settings, session, photo_file_id=photo_id)
    else:
        language = await language_for(db, message.from_user.id)
        await message.answer(tr(language, "photo_required"))


@router.message(F.text, HasUserSession())
async def user_text(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    if not message.from_user or not message.text:
        return
    session = await db.get_session(message.from_user.id, "user")
    if not session:
        return
    state, payload = session
    language = await language_for(db, message.from_user.id)
    if state == "topup_amount_dzd" or state == "topup_amount_usd":
        try:
            amount = Decimal(message.text.strip().replace(",", "."))
        except InvalidOperation:
            await message.answer(tr(language, "invalid_amount"))
            return
        currency = "DZD" if state.endswith("dzd") else "USD"
        await prepare_topup(message, db, message.from_user.id, currency, amount, language)
    elif state == "topup_dzd_receipt":
        await message.answer(tr(language, "send_receipt"))
    elif state == "topup_usd_proof":
        txid = message.text.strip()
        if not txid or len(txid) > 500:
            await message.answer(tr(language, "send_txid_or_photo"))
            return
        await save_topup_submission(message, bot, db, settings, session, txid=txid)
    elif state == "coupon_code":
        try:
            draft_id = uuid.UUID(str(payload["draft_id"]))
            draft = await db.apply_coupon_to_draft(
                message.from_user.id, draft_id, message.text.strip()
            )
        except StoreError as exc:
            key = {
                "coupon_exhausted": "coupon_exhausted",
                "coupon_currency_mismatch": "coupon_currency_mismatch",
            }.get(str(exc), "coupon_invalid")
            await message.answer(tr(language, key))
            return
        except (ValueError, InvalidOperation, KeyError):
            await message.answer(tr(language, "coupon_invalid"))
            return
        await db.clear_session(message.from_user.id, "user")
        subtotal = money(draft["price"])
        discount = money(draft["coupon_discount"])
        total = money(subtotal - discount)
        summary = tr(
            language,
            "checkout",
            product=escape(draft["product_name"]),
            months=draft["duration_months"],
            price=amount_text(total, draft["currency"]),
            currency=draft["currency"],
        )
        summary += "\n\n" + tr(
            language,
            "coupon_applied",
            code=escape(draft["coupon_code"]),
            subtotal=amount_text(subtotal, draft["currency"]),
            discount=amount_text(discount, draft["currency"]),
            total=amount_text(total, draft["currency"]),
            currency=draft["currency"],
        )
        await message.answer(
            summary,
            reply_markup=inline([
                [(tr(language, "confirm"), f"u:confirm:{draft_id}"),
                 (tr(language, "cancel"), "u:cancel")],
                [("🎟️ تغيير الكوبون" if language == "ar" else "🎟️ Change coupon",
                  f"u:coupon:{draft_id}"),
                 ("إزالة الكوبون" if language == "ar" else "Remove coupon",
                  f"u:coupon_remove:{draft_id}")],
            ]),
        )
    elif state == "gift_details":
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer(tr(language, "gift_invalid"))
            return
        recipient_ref, raw_amount = parts
        try:
            amount = Decimal(raw_amount.replace(",", "."))
            if (
                not amount.is_finite()
                or amount < 150
                or amount != amount.quantize(Decimal("1"))
            ):
                raise ValueError("gift_amount")
            if recipient_ref.startswith("@"):
                recipient = await db.find_user_by_username(recipient_ref)
            elif recipient_ref.isdecimal():
                recipient = await db.get_user(int(recipient_ref))
            else:
                await message.answer(tr(language, "gift_invalid"))
                return
        except StoreError as exc:
            if str(exc) == "ambiguous_recipient":
                await message.answer(tr(language, "gift_ambiguous"))
            else:
                await message.answer(tr(language, "gift_invalid"))
            return
        except (ValueError, InvalidOperation, OverflowError):
            await message.answer(tr(language, "gift_invalid"))
            return
        if not recipient:
            await message.answer(tr(language, "gift_recipient_missing"))
            return
        if recipient["telegram_id"] == message.from_user.id:
            await message.answer(tr(language, "gift_self"))
            return
        try:
            draft = await db.create_gift_draft(
                message.from_user.id, recipient["telegram_id"], amount
            )
        except StoreError:
            await message.answer(tr(language, "gift_invalid"))
            return
        await db.clear_session(message.from_user.id, "user")
        recipient_name = escape(
            f"@{recipient['username']}" if recipient["username"] else recipient["first_name"]
        )
        await message.answer(
            tr(
                language,
                "gift_confirm",
                amount=amount_text(money(amount), "DZD"),
                recipient=recipient_name,
            ),
            reply_markup=inline([
                [("تأكيد الهدية" if language == "ar" else "Confirm gift",
                  f"u:gift_confirm:{draft['id']}")],
                [(tr(language, "cancel"), f"u:gift_cancel:{draft['id']}")],
            ]),
        )
    elif state == "support":
        await save_support_message(message, bot, db, settings, session, text=message.text.strip())


async def save_support_message(
    message: Message,
    bot: Bot,
    db: Database,
    settings: Settings,
    session: tuple[str, dict],
    text: str | None = None,
    photo_file_id: str | None = None,
) -> None:
    if not message.from_user:
        return
    _, payload = session
    language = await language_for(db, message.from_user.id)
    try:
        ticket_id = int(payload["ticket_id"])
        ticket = await db.ticket_detail(ticket_id)
        if not ticket or ticket["status"] != "open":
            await db.clear_session(message.from_user.id, "user")
            await message.answer(tr(language, "support_closed"))
            return
        await db.add_support_message(ticket_id, message.from_user.id, "user", text, photo_file_id)
        await message.answer(tr(language, "support_saved", code=escape(ticket["ticket_code"])))
        caption = (
            f"🎧 <b>رسالة دعم {escape(ticket['ticket_code'])}</b>\n"
            f"المستخدم: {escape(ticket['first_name'] or '')} @{escape(ticket['username'] or '—')}\n"
            f"Telegram ID: <code>{message.from_user.id}</code>\n"
            f"{escape(text or 'صورة مرفقة')}"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    caption,
                    reply_markup=inline([[("الرد", f"a:ticket:{ticket_id}")]]),
                )
                if photo_file_id:
                    await bot.send_photo(admin_id, photo_file_id, caption=ticket["ticket_code"])
            except Exception:
                log.info("Support message persisted; admin may retrieve it from the panel.")
    except Exception:
        log.exception("Could not save support message.")
        await message.answer(tr(language, "generic_error"))

