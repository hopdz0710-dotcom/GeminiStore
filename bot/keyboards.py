from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.i18n import tr


def main_menu(language: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tr(language, "menu_store")), KeyboardButton(text=tr(language, "menu_topup"))],
            [KeyboardButton(text=tr(language, "menu_orders")), KeyboardButton(text=tr(language, "menu_support"))],
            [KeyboardButton(text=tr(language, "menu_settings"))],
            [KeyboardButton(text=tr(language, "menu_referral")), KeyboardButton(text=tr(language, "menu_gift"))],
        ],
        resize_keyboard=True,
        input_field_placeholder="Gemini Store",
    )


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return inline([
        [("📊 الإحصائيات", "a:stats"), ("🛍 المنتجات والأسعار", "a:products")],
        [("💳 طلبات الشحن", "a:topups"), ("📦 طلبات الشراء", "a:orders")],
        [("👤 المستخدمون والمحافظ", "a:users"), ("🧾 الاشتراكات", "a:subs")],
        [("🎧 الدعم الفني", "a:support"), ("💵 إعدادات Binance", "a:payments")],
        [("🎟️ الكوبونات", "a:coupons")],
        [("🛠 وضع الصيانة", "a:maintenance")],
    ])


def admin_back() -> InlineKeyboardMarkup:
    return inline([[("⬅️ لوحة الإدارة", "a:home")]])
