from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.db import Database, StoreError, money
from bot.i18n import tr
from bot.keyboards import admin_back, admin_menu, inline, main_menu

log = logging.getLogger(__name__)
router = Router(name="admin")

ADMIN_STATUS = {
    "pending_review": "قيد مراجعة الإدارة",
    "approved_waiting_invite": "مقبول — بانتظار رابط الدعوة",
    "link_sent": "تم إرسال رابط الدعوة",
    "active": "اشتراك نشط",
    "expired": "اشتراك منتهي",
}


def fmt_amount(value, currency: str) -> str:
    amount = money(value)
    return f"{amount.quantize(Decimal('1')):f}" if currency == "DZD" else f"{amount.normalize():f}"


def fmt_date(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def user_name(row) -> str:
    name = escape(row["first_name"] or "")
    username = escape(row["username"] or "")
    return f"{name} (@{username})" if username else name


async def admin_ok(message: Message | CallbackQuery, settings: Settings) -> bool:
    user = message.from_user
    if not user or user.id not in settings.admin_ids:
        if isinstance(message, CallbackQuery):
            await message.answer("غير مصرح لك.")
        else:
            await message.answer("غير مصرح لك.")
        return False
    chat = message.message.chat if isinstance(message, CallbackQuery) and message.message else message.chat if isinstance(message, Message) else None
    if chat and chat.type != "private":
        if isinstance(message, CallbackQuery):
            await message.answer("استخدم لوحة الإدارة في محادثة خاصة مع البوت.")
        else:
            await message.answer("استخدم لوحة الإدارة في محادثة خاصة مع البوت.")
        return False
    return True


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database, settings: Settings) -> None:
    if not await admin_ok(message, settings):
        return
    await message.answer(
        "لوحة تحكم Gemini Store",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data.startswith("a:"))
async def admin_callback(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> None:
    if not await admin_ok(callback, settings):
        return
    if not callback.message:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    admin_id = callback.from_user.id
    try:
        if action == "home":
            await callback.message.answer("لوحة تحكم Gemini Store", reply_markup=admin_menu())
        elif action == "stats":
            stats = await db.admin_stats()
            await callback.message.answer(
                "<b>الإحصائيات الأساسية</b>\n"
                f"المستخدمون: {stats['users']}\n"
                f"الطلبات: {stats['orders']}\n"
                f"الاشتراكات النشطة: {stats['active_subscriptions']}\n"
                f"طلبات الشحن المعلقة: {stats['pending_topups']}\n"
                f"طلبات الشراء المعلقة: {stats['pending_orders']}\n"
                f"تذاكر الدعم المفتوحة: {stats['open_tickets']}",
                reply_markup=admin_back(),
            )
        elif action == "products":
            await show_products(callback.message, db)
        elif action == "product":
            await show_product(callback.message, db, int(parts[2]))
        elif action == "prod_toggle":
            product_id = int(parts[2])
            product = await db.get_product(product_id)
            if not product:
                raise StoreError("missing")
            await db.set_product_active(product_id, not product["active"], admin_id)
            await show_product(callback.message, db, product_id)
        elif action == "price":
            product_id, months, currency = int(parts[2]), int(parts[3]), parts[4]
            await db.set_session(admin_id, "admin", "set_price", {
                "product_id": product_id, "duration": months, "currency": currency,
            })
            await callback.message.answer(
                f"أرسل السعر الجديد: المنتج #{product_id}، المدة {months} شهر، العملة {currency}.\n"
                + ("سعر DZD يجب أن يكون عدداً صحيحاً." if currency == "DZD" else "استخدم حتى منزلتين عشريتين."),
                reply_markup=admin_back(),
            )
        elif action == "bulk":
            await callback.message.answer(
                "اختر العملة التي تريد تعديل أسعارها كلها:",
                reply_markup=inline([[
                    ("تعديل أسعار DZD", "a:bulk_currency:DZD"),
                    ("تعديل أسعار USD", "a:bulk_currency:USD"),
                ], [("٪ تعديل جميع الأسعار", "a:bulk")]]),
            )
        elif action == "bulk_currency":
            currency = parts[2]
            await db.set_session(admin_id, "admin", "bulk_percent", {"currency": currency})
            await callback.message.answer(
                f"أرسل نسبة التعديل على جميع أسعار {currency} كرقم موقّع، مثل +10 أو -5.\n"
                "يُطبّق التعديل على الأسعار المضبوطة فقط."
            )
        elif action == "topups":
            await show_topups(callback.message, db)
        elif action == "topup":
            await show_topup(callback.message, db, int(parts[2]))
        elif action == "topup_accept":
            request_id = int(parts[2])
            request = await db.decide_topup(request_id, admin_id, True)
            if not request:
                await callback.message.answer("تمت معالجة هذا الطلب مسبقاً أو لم يعد معلقاً.")
            else:
                await send_user_notice(bot, db, request["user_id"],
                                       f"تم قبول طلب الشحن {escape(request['request_code'])} وإضافة الرصيد إلى محفظتك.")
                await callback.message.answer("تم قبول طلب الشحن وإضافة المبلغ مرة واحدة.")
        elif action == "topup_reject":
            request_id = int(parts[2])
            await db.set_session(admin_id, "admin", "topup_rejection", {"request_id": request_id})
            await callback.message.answer("أرسل سبب رفض طلب الشحن. لن يُضاف أي رصيد.")
        elif action == "orders":
            await show_orders(callback.message, db)
        elif action == "order":
            await show_order(callback.message, db, int(parts[2]))
        elif action == "order_accept":
            order_id = int(parts[2])
            order = await db.approve_order(order_id, admin_id)
            if not order:
                await callback.message.answer("هذا الطلب لم يعد بانتظار المراجعة.")
            else:
                await db.set_session(admin_id, "admin", "invite_link", {"order_id": order_id})
                await callback.message.answer(
                    f"تم قبول الطلب {escape(order['order_code'])}. أرسل رابط الدعوة للتحقق منه وإرساله للمشتري فقط."
                )
        elif action == "order_link":
            order_id = int(parts[2])
            order = await db.order_detail(order_id)
            if not order or order["status"] != "approved_waiting_invite":
                await callback.message.answer("الطلب غير جاهز لإرسال رابط.")
            elif order["invite_url"]:
                await deliver_invite(callback.message, bot, db, order, admin_id)
            else:
                await db.set_session(admin_id, "admin", "invite_link", {"order_id": order_id})
                await callback.message.answer("أرسل رابط الدعوة لهذا الطلب.")
        elif action == "order_join":
            order_id = int(parts[2])
            order = await db.activate_order(order_id, admin_id)
            if not order:
                await callback.message.answer("لا يمكن تأكيد الانضمام لهذا الطلب؛ تحقق من حالته.")
            else:
                await send_user_notice(
                    bot, db, order["user_id"],
                    f"تم تأكيد تفعيل اشتراكك للطلب {escape(order['order_code'])}.\n"
                    f"تاريخ التفعيل: {fmt_date(order['joined_at'])}\n"
                    f"تاريخ الانتهاء: {fmt_date(order['expires_at'])}",
                )
                await callback.message.answer(
                    f"تم تسجيل الانضمام الفعلي. ينتهي الاشتراك في {fmt_date(order['expires_at'])}."
                )
        elif action == "users":
            await db.set_session(admin_id, "admin", "find_user", {})
            await callback.message.answer("أرسل Telegram ID للمستخدم للبحث عن بياناته ومحافظه.")
        elif action == "user":
            await show_user(callback.message, db, int(parts[2]))
        elif action == "wallet_adjust":
            telegram_id, currency = int(parts[2]), parts[3]
            await db.set_session(admin_id, "admin", "wallet_adjust", {
                "user_id": telegram_id, "currency": currency,
            })
            await callback.message.answer(
                f"أرسل التعديل والسبب بهذا الشكل: +500|تصحيح رصيد ({currency}).\n"
                "استخدم إشارة سالبة للخصم. لا يمكن أن يصبح الرصيد سالباً."
            )
        elif action == "subs":
            await show_subscriptions(callback.message, db)
        elif action == "support":
            await show_tickets(callback.message, db)
        elif action == "ticket":
            await show_ticket(callback.message, db, int(parts[2]))
        elif action == "ticket_reply":
            ticket_id = int(parts[2])
            ticket = await db.ticket_detail(ticket_id)
            if not ticket or ticket["status"] != "open":
                await callback.message.answer("التذكرة غير مفتوحة.")
            else:
                await db.set_session(admin_id, "admin", "support_reply", {"ticket_id": ticket_id})
                await callback.message.answer("أرسل ردك نصاً أو صورة. سيصل إلى صاحب التذكرة فقط.")
        elif action == "ticket_close":
            ticket_id = int(parts[2])
            closed = await db.close_ticket(ticket_id, admin_id)
            if closed:
                ticket = await db.ticket_detail(ticket_id)
                await send_user_notice(bot, db, ticket["user_id"], "تم إغلاق تذكرة الدعم الخاصة بك.")
                await callback.message.answer("تم إغلاق التذكرة.")
            else:
                await callback.message.answer("التذكرة مغلقة مسبقاً.")
        elif action == "payments":
            await show_payment_settings(callback.message, db)
        elif action == "payment":
            key = parts[2]
            if key not in {"binance_address", "binance_network", "binance_currency", "binance_instructions"}:
                raise StoreError("invalid_setting")
            await db.set_session(admin_id, "admin", "setting_value", {"key": key})
            labels = {
                "binance_address": "عنوان Binance",
                "binance_network": "شبكة التحويل",
                "binance_currency": "العملة (مثل USDT)",
                "binance_instructions": "تعليمات التحويل",
            }
            await callback.message.answer(f"أرسل {labels[key]} الجديدة.")
        elif action == "maintenance":
            enabled = await db.setting("maintenance") == "true"
            await db.set_setting("maintenance", "false" if enabled else "true", admin_id)
            await callback.message.answer("تم إيقاف وضع الصيانة." if enabled else "تم تفعيل وضع الصيانة.")
            await callback.message.answer("لوحة تحكم Gemini Store", reply_markup=admin_menu())
    except (ValueError, IndexError, StoreError):
        await callback.message.answer("تعذر إتمام الإجراء. تحقق من البيانات وحالة الطلب.")
    except Exception:
        log.exception("Admin callback failed.")
        await callback.message.answer("حدث خطأ غير متوقع. لم يتم تأكيد الإجراء؛ تحقق من الحالة قبل إعادة المحاولة.")
    finally:
        await callback.answer()


async def show_products(message: Message, db: Database) -> None:
    products = await db.list_products(active_only=False)
    rows = [[(f"{'●' if p['active'] else '○'} {p['name']}", f"a:product:{p['id']}")] for p in products]
    rows.extend([
        [("تعديل جميع الأسعار بنسبة ٪", "a:bulk")],
        [("⬅️ لوحة الإدارة", "a:home")],
    ])
    await message.answer("<b>إدارة المنتجات والأسعار</b>\nاضغط منتجاً لتعديل أسعاره أو تشغيله.",
                         reply_markup=inline(rows))


async def show_product(message: Message, db: Database, product_id: int) -> None:
    product = await db.get_product(product_id)
    if not product:
        await message.answer("المنتج غير موجود.")
        return
    prices = await db.get_prices(product_id)
    table: dict[tuple[int, str], object] = {
        (row["duration_months"], row["currency"]): row for row in prices
    }
    rows: list[list[tuple[str, str]]] = []
    for months in (1, 3, 6, 12):
        row_buttons = []
        for currency in ("DZD", "USD"):
            row = table.get((months, currency))
            value = fmt_amount(row["amount"], currency) if row and row["configured"] else "غير مضبوط"
            row_buttons.append((f"{months} شهر — {value} {currency}", f"a:price:{product_id}:{months}:{currency}"))
        rows.append(row_buttons)
    rows.append([(
        "إيقاف المنتج" if product["active"] else "تشغيل المنتج",
        f"a:prod_toggle:{product_id}",
    )])
    rows.append([("⬅️ المنتجات", "a:products")])
    await message.answer(
        f"<b>{escape(product['name'])}</b>\nالحالة: {'يعمل' if product['active'] else 'متوقف'}\n"
        "اختر مدة وعملة لتعديل سعرها.",
        reply_markup=inline(rows),
    )


async def show_topups(message: Message, db: Database) -> None:
    items = await db.admin_topups("pending")
    if not items:
        await message.answer("لا توجد طلبات شحن معلقة.", reply_markup=admin_back())
        return
    rows = [[(
        f"{row['request_code']} · {fmt_amount(row['amount'], row['currency'])} {row['currency']}",
        f"a:topup:{row['id']}",
    )] for row in items]
    rows.append([("⬅️ لوحة الإدارة", "a:home")])
    await message.answer("طلبات الشحن المعلقة:", reply_markup=inline(rows))


async def show_topup(message: Message, db: Database, request_id: int) -> None:
    request = await db.topup_detail(request_id)
    if not request:
        await message.answer("طلب الشحن غير موجود.")
        return
    text = (
        f"<b>طلب شحن {escape(request['request_code'])}</b>\n"
        f"المستخدم: {user_name(request)}\nTelegram ID: <code>{request['user_id']}</code>\n"
        f"المبلغ: {fmt_amount(request['amount'], request['currency'])} {request['currency']}\n"
        f"TXID: <code>{escape(request['txid'] or '—')}</code>\n"
        f"الحالة: {escape(request['status'])}\nالتاريخ: {fmt_date(request['created_at'])}"
    )
    rows = []
    if request["status"] == "pending":
        rows.append([
            ("قبول الشحن", f"a:topup_accept:{request_id}"),
            ("رفض مع السبب", f"a:topup_reject:{request_id}"),
        ])
    rows.append([("⬅️ طلبات الشحن", "a:topups")])
    await message.answer(text, reply_markup=inline(rows))
    if request["receipt_file_id"]:
        await message.answer_photo(request["receipt_file_id"], caption=request["request_code"])


async def show_orders(message: Message, db: Database) -> None:
    orders = await db.admin_orders(
        ["pending_review", "approved_waiting_invite", "link_sent", "active", "expired"],
        limit=25,
    )
    if not orders:
        await message.answer("لا توجد طلبات شراء.", reply_markup=admin_back())
        return
    rows = [[(
        f"{row['order_code']} · {row['product_name']} · {ADMIN_STATUS[row['status']]}",
        f"a:order:{row['id']}",
    )] for row in orders]
    rows.append([("⬅️ لوحة الإدارة", "a:home")])
    await message.answer("طلبات الشراء والاشتراكات:", reply_markup=inline(rows))


async def show_order(message: Message, db: Database, order_id: int) -> None:
    order = await db.order_detail(order_id)
    if not order:
        await message.answer("الطلب غير موجود.")
        return
    text = (
        f"<b>طلب {escape(order['order_code'])}</b>\n"
        f"Telegram ID: <code>{order['user_id']}</code>\n"
        f"المستخدم: {user_name(order)}\n"
        f"المنتج: {escape(order['product_name'])} — {order['duration_months']} شهر\n"
        f"السعر: {fmt_amount(order['price'], order['currency'])} {order['currency']}\n"
        f"تاريخ الطلب: {fmt_date(order['created_at'])}\n"
        f"الحالة: {ADMIN_STATUS[order['status']]}"
    )
    rows = []
    if order["status"] == "pending_review":
        rows.append([("قبول الطلب", f"a:order_accept:{order_id}")])
    elif order["status"] == "approved_waiting_invite":
        rows.append([(
            "إرسال رابط محفوظ" if order["invite_url"] else "إدخال رابط الدعوة",
            f"a:order_link:{order_id}",
        )])
    elif order["status"] == "link_sent":
        rows.append([("تأكيد انضمام المستخدم", f"a:order_join:{order_id}")])
    rows.append([("⬅️ طلبات الشراء", "a:orders")])
    await message.answer(text, reply_markup=inline(rows))


async def show_user(message: Message, db: Database, telegram_id: int) -> None:
    user = await db.find_user(telegram_id)
    if not user:
        await message.answer("لم يتم العثور على مستخدم بهذا المعرّف.", reply_markup=admin_back())
        return
    text = (
        f"<b>المستخدم</b>\nTelegram ID: <code>{user['telegram_id']}</code>\n"
        f"الاسم: {user_name(user)}\nاللغة: {escape(user['language'])}\n"
        f"تاريخ التسجيل: {fmt_date(user['created_at'])}\n"
        f"محفظة DZD: {fmt_amount(user['dzd_balance'], 'DZD')}\n"
        f"محفظة USD: {fmt_amount(user['usd_balance'], 'USD')}"
    )
    await message.answer(text, reply_markup=inline([
        [
            ("تعديل DZD", f"a:wallet_adjust:{telegram_id}:DZD"),
            ("تعديل USD", f"a:wallet_adjust:{telegram_id}:USD"),
        ],
        [("بحث عن مستخدم آخر", "a:users")],
    ]))


async def show_subscriptions(message: Message, db: Database) -> None:
    items = await db.admin_subscriptions()
    rows = []
    for sub in items:
        rows.append([(
            f"{sub['order_code']} · {sub['product_name']} · {ADMIN_STATUS[sub['status']]}",
            f"a:order:{sub['order_id']}",
        )])
    rows.append([("طلبات بانتظار تأكيد الانضمام", "a:orders")])
    rows.append([("⬅️ لوحة الإدارة", "a:home")])
    await message.answer("الاشتراكات المسجلة:", reply_markup=inline(rows))


async def show_tickets(message: Message, db: Database) -> None:
    tickets = await db.open_tickets()
    if not tickets:
        await message.answer("لا توجد تذاكر دعم مفتوحة.", reply_markup=admin_back())
        return
    rows = [[(
        f"{row['ticket_code']} · {user_name(row)} · {row['messages']} رسائل",
        f"a:ticket:{row['id']}",
    )] for row in tickets]
    rows.append([("⬅️ لوحة الإدارة", "a:home")])
    await message.answer("تذاكر الدعم المفتوحة:", reply_markup=inline(rows))


async def show_ticket(message: Message, db: Database, ticket_id: int) -> None:
    ticket = await db.ticket_detail(ticket_id)
    if not ticket:
        await message.answer("التذكرة غير موجودة.")
        return
    messages = await db.ticket_messages(ticket_id)
    excerpt = []
    for row in reversed(messages):
        prefix = "المستخدم" if row["sender_role"] == "user" else "الإدارة"
        excerpt.append(f"<b>{prefix}:</b> {escape(row['text'] or '[صورة]')}")
    rows = [[("الرد على المستخدم", f"a:ticket_reply:{ticket_id}")]]
    if ticket["status"] == "open":
        rows.append([("إغلاق التذكرة", f"a:ticket_close:{ticket_id}")])
    rows.append([("⬅️ تذاكر الدعم", "a:support")])
    await message.answer(
        f"<b>{escape(ticket['ticket_code'])}</b> · {user_name(ticket)}\n"
        f"Telegram ID: <code>{ticket['user_id']}</code>\n\n"
        + ("\n".join(excerpt) or "لا توجد رسائل."),
        reply_markup=inline(rows),
    )
    for row in messages:
        if row["photo_file_id"]:
            await message.answer_photo(row["photo_file_id"], caption=f"{ticket['ticket_code']} · {row['sender_role']}")


async def show_payment_settings(message: Message, db: Database) -> None:
    address, network, coin, instructions = [
        await db.setting(key)
        for key in ("binance_address", "binance_network", "binance_currency", "binance_instructions")
    ]
    text = (
        "<b>بيانات Binance لشحن USD</b>\n"
        f"العنوان: <code>{escape(address or 'غير مضبوط')}</code>\n"
        f"الشبكة: {escape(network or 'غير مضبوط')}\n"
        f"العملة: {escape(coin or 'غير مضبوط')}\n"
        f"التعليمات: {escape(instructions or 'غير مضبوطة')}"
    )
    await message.answer(text, reply_markup=inline([
        [("تعديل العنوان", "a:payment:binance_address"), ("تعديل الشبكة", "a:payment:binance_network")],
        [("تعديل العملة", "a:payment:binance_currency"), ("تعديل التعليمات", "a:payment:binance_instructions")],
        [("⬅️ لوحة الإدارة", "a:home")],
    ]))


async def send_user_notice(bot: Bot, db: Database, telegram_id: int, text: str) -> None:
    try:
        user = await db.get_user(telegram_id)
        if user:
            await bot.send_message(telegram_id, text, reply_markup=main_menu(user["language"]))
    except Exception:
        log.info("Could not deliver a user notification.")


async def deliver_invite(message: Message, bot: Bot, db: Database, order, admin_id: int) -> None:
    url = order["invite_url"]
    if not url:
        await message.answer("أدخل رابط الدعوة أولاً.")
        return
    try:
        await bot.send_message(
            order["user_id"],
            f"تم قبول طلبك {escape(order['order_code'])}.\nرابط الدعوة الخاص بطلبك:\n{escape(url)}\n"
            "يبدأ الاشتراك بعد تأكيد الإدارة انضمامك فعلياً.",
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            "تعذر إرسال الرابط إلى المستخدم. بقي الطلب بانتظار إرسال الرابط؛ "
            "تأكد من أن المستخدم بدأ البوت ثم أعد المحاولة."
        )
        return
    await db.mark_invite_sent(order["id"])
    await message.answer("تم إرسال رابط الدعوة إلى صاحب الطلب فقط.")


def is_valid_invite_url(value: str) -> bool:
    if len(value) > 1024:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password


@router.message(F.text)
async def admin_text_input(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    if not message.from_user or not message.text or message.chat.type != "private":
        return
    if message.from_user.id not in settings.admin_ids:
        return
    session = await db.get_session(message.from_user.id, "admin")
    if not session:
        return
    state, payload = session
    value = message.text.strip()
    admin_id = message.from_user.id
    try:
        if state == "topup_rejection":
            if len(value) < 3 or len(value) > 500:
                await message.answer("أرسل سبباً من 3 إلى 500 حرف.")
                return
            request = await db.decide_topup(int(payload["request_id"]), admin_id, False, value)
            await db.clear_session(admin_id, "admin")
            if not request:
                await message.answer("تمت معالجة الطلب مسبقاً.")
            else:
                await send_user_notice(
                    bot, db, request["user_id"],
                    f"تم رفض طلب الشحن {escape(request['request_code'])}.\nالسبب: {escape(value)}",
                )
                await message.answer("تم رفض طلب الشحن مع تسجيل السبب.")
        elif state == "invite_link":
            order_id = int(payload["order_id"])
            if not is_valid_invite_url(value):
                await message.answer("الرابط غير صالح. أرسل رابطاً يبدأ بـ https:// .")
                return
            order = await db.store_invite(order_id, admin_id, value)
            if not order:
                await db.clear_session(admin_id, "admin")
                await message.answer("الطلب لم يعد بانتظار رابط دعوة.")
                return
            await db.clear_session(admin_id, "admin")
            await deliver_invite(message, bot, db, order, admin_id)
        elif state == "set_price":
            amount = parse_amount(value)
            if amount <= 0:
                raise ValueError("price")
            if payload["currency"] == "DZD" and amount != amount.quantize(Decimal("1")):
                await message.answer("يجب أن يكون سعر DZD عدداً صحيحاً.")
                return
            await db.set_product_price(
                int(payload["product_id"]),
                int(payload["duration"]),
                payload["currency"],
                amount,
                admin_id,
            )
            await db.clear_session(admin_id, "admin")
            await message.answer("تم حفظ السعر وتسجيل العملية.")
        elif state == "bulk_percent":
            percent = Decimal(value.replace("%", "").replace(",", "."))
            rows = await db.adjust_all_prices(payload["currency"], percent, admin_id)
            await db.clear_session(admin_id, "admin")
            await message.answer(f"تم تعديل {rows} سعراً من أسعار {payload['currency']} بنسبة {percent}%.")
        elif state == "find_user":
            telegram_id = int(value)
            if telegram_id <= 0:
                raise ValueError("id")
            await db.clear_session(admin_id, "admin")
            await show_user(message, db, telegram_id)
        elif state == "wallet_adjust":
            if "|" not in value:
                await message.answer("استخدم الصيغة +500|سبب التعديل")
                return
            raw_amount, reason = value.split("|", 1)
            amount = parse_amount(raw_amount)
            reason = reason.strip()
            currency = payload["currency"]
            if currency == "DZD" and amount != amount.quantize(Decimal("1")):
                await message.answer("يجب أن يكون تعديل DZD عدداً صحيحاً.")
                return
            if not reason or len(reason) > 300:
                await message.answer("أضف سبباً من 1 إلى 300 حرف.")
                return
            ok = await db.adjust_wallet(
                int(payload["user_id"]), currency, amount, reason, admin_id
            )
            await db.clear_session(admin_id, "admin")
            if not ok:
                await message.answer("لم يُنفذ التعديل؛ المستخدم غير موجود أو سيصبح رصيده سالباً.")
            else:
                await message.answer("تم تعديل المحفظة وتسجيل الحركة في السجل.")
                await show_user(message, db, int(payload["user_id"]))
        elif state == "setting_value":
            key = payload["key"]
            if len(value) > 1000 or not value:
                await message.answer("القيمة غير صالحة.")
                return
            await db.set_setting(key, value, admin_id)
            await db.clear_session(admin_id, "admin")
            await message.answer("تم حفظ الإعداد وتسجيل العملية.")
        elif state == "support_reply":
            await reply_to_ticket(message, bot, db, payload, admin_id, text=value)
    except (ValueError, InvalidOperation, StoreError):
        await message.answer("القيمة غير صالحة. لم يتم حفظ التعديل.")
    except Exception:
        log.exception("Admin text action failed.")
        await message.answer("تعذر إتمام الإجراء. تحقق من البيانات والحالة قبل المحاولة مجدداً.")


@router.message(F.photo)
async def admin_photo_reply(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    if not message.from_user or message.chat.type != "private" or message.from_user.id not in settings.admin_ids:
        return
    session = await db.get_session(message.from_user.id, "admin")
    if not session or session[0] != "support_reply":
        return
    await reply_to_ticket(
        message,
        bot,
        db,
        session[1],
        message.from_user.id,
        text=message.caption,
        photo_file_id=message.photo[-1].file_id,
    )


def parse_amount(value: str) -> Decimal:
    amount = Decimal(value.strip().replace(",", "."))
    if not amount.is_finite():
        raise ValueError("amount")
    return money(amount)


async def reply_to_ticket(
    message: Message,
    bot: Bot,
    db: Database,
    payload: dict,
    admin_id: int,
    text: str | None = None,
    photo_file_id: str | None = None,
) -> None:
    ticket_id = int(payload["ticket_id"])
    ticket = await db.ticket_detail(ticket_id)
    if not ticket or ticket["status"] != "open":
        await db.clear_session(admin_id, "admin")
        await message.answer("التذكرة لم تعد مفتوحة.")
        return
    try:
        if photo_file_id:
            await bot.send_photo(ticket["user_id"], photo_file_id, caption=text or None)
        else:
            await bot.send_message(ticket["user_id"], text or "")
    except Exception:
        await message.answer("تعذر إرسال الرد. تأكد أن المستخدم بدأ البوت.")
        return
    await db.add_support_message(ticket_id, admin_id, "admin", text, photo_file_id)
    await db.clear_session(admin_id, "admin")
    await message.answer("تم إرسال الرد إلى صاحب التذكرة وحفظه.")

