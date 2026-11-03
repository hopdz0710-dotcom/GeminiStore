from __future__ import annotations

from decimal import Decimal
from html import escape

from bot.db import money
from bot.i18n import tr


def format_amount(value: Decimal, currency: str) -> str:
    amount = money(value)
    return f"{amount.quantize(Decimal('1')):f}" if currency == "DZD" else f"{amount.normalize():f}"


def format_order_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def purchase_confirmation(order, language: str) -> str:
    return tr(
        language,
        "stage_purchase",
        product=escape(order["product_name"]),
        months=order["duration_months"],
        amount=format_amount(order["price"], order["currency"]),
        currency=order["currency"],
        code=escape(order["order_code"]),
    )


def approval_notice(order, language: str) -> str:
    return tr(
        language,
        "stage_approved",
        product=escape(order["product_name"]),
        code=escape(order["order_code"]),
        time=format_order_time(order["created_at"]),
    )


def activation_delivery(order, language: str) -> str:
    return tr(
        language,
        "stage_delivery",
        product=escape(order["product_name"]),
        quantity=order["quantity"],
        amount=format_amount(order["price"], order["currency"]),
        currency=order["currency"],
        code=escape(order["order_code"]),
        time=format_order_time(order["delivered_at"]),
        activation_code=escape(order["activation_code"]),
    )
