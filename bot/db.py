from __future__ import annotations

import calendar
import json
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import asyncpg


class StoreError(Exception):
    """Expected business-rule error safe to show to the user."""


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "Database":
        pool = await asyncpg.create_pool(
            database_url,
            min_size=1,
            max_size=10,
            command_timeout=30,
            server_settings={"application_name": "gemini-store-bot"},
        )
        db = cls(pool)
        await db.verify_schema()
        return db

    async def close(self) -> None:
        await self.pool.close()

    async def verify_schema(self) -> None:
        async with self.pool.acquire() as conn:
            found = await conn.fetchval("SELECT to_regclass('store_users')")
        if not found:
            raise RuntimeError(
                "Gemini Store database tables are missing. Run `python -m bot.migrate` once before starting the bot."
            )

    async def register_user(self, telegram_id: int, username: str | None, first_name: str) -> tuple[str, bool]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                created_id = await conn.fetchval(
                    """
                    INSERT INTO store_users (telegram_id, username, first_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (telegram_id) DO NOTHING
                    RETURNING telegram_id
                    """,
                    telegram_id,
                    username,
                    first_name,
                )
                if created_id is None:
                    await conn.execute(
                        """
                        UPDATE store_users SET username = $2, first_name = $3, updated_at = NOW()
                        WHERE telegram_id = $1
                        """,
                        telegram_id,
                        username,
                        first_name,
                    )
                await conn.execute(
                    """
                    INSERT INTO wallets (user_id, currency) VALUES ($1, 'DZD'), ($1, 'USD')
                    ON CONFLICT (user_id, currency) DO NOTHING
                    """,
                    telegram_id,
                )
                if created_id is not None:
                    await conn.execute(
                        "UPDATE wallets SET balance = balance + 20, updated_at = NOW() WHERE user_id = $1 AND currency = 'DZD'",
                        telegram_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO wallet_ledger (user_id, currency, amount, transaction_type, reference, details)
                        VALUES ($1, 'DZD', 20, 'signup_reward', $2, '{}'::jsonb)
                        ON CONFLICT (transaction_type, reference, user_id) DO NOTHING
                        """,
                        telegram_id,
                        str(telegram_id),
                    )
                language = await conn.fetchval("SELECT language FROM store_users WHERE telegram_id = $1", telegram_id)
        return language or "ar", created_id is not None

    async def get_user(self, telegram_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM store_users WHERE telegram_id = $1", telegram_id)

    async def set_language(self, telegram_id: int, language: str) -> None:
        if language not in {"ar", "en"}:
            raise StoreError("Unsupported language.")
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE store_users SET language = $2, updated_at = NOW() WHERE telegram_id = $1",
                telegram_id,
                language,
            )

    async def wallet_balances(self, telegram_id: int) -> dict[str, Decimal]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT currency, balance FROM wallets WHERE user_id = $1", telegram_id)
        return {row["currency"]: money(row["balance"]) for row in rows}

    async def list_products(self, active_only: bool = True) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if active_only:
                return await conn.fetch("SELECT * FROM products WHERE active = TRUE ORDER BY id")
            return await conn.fetch("SELECT * FROM products ORDER BY id")

    async def get_product(self, product_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)

    async def get_prices(self, product_id: int, duration_months: int | None = None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if duration_months is None:
                return await conn.fetch(
                    "SELECT * FROM product_prices WHERE product_id = $1 ORDER BY duration_months, currency",
                    product_id,
                )
            return await conn.fetch(
                """
                SELECT * FROM product_prices
                WHERE product_id = $1 AND duration_months = $2 ORDER BY currency
                """,
                product_id,
                duration_months,
            )

    async def set_product_active(self, product_id: int, active: bool, admin_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    "UPDATE products SET active = $2, updated_at = NOW() WHERE id = $1",
                    product_id,
                    active,
                )
                if result.endswith("1"):
                    await self._audit(conn, admin_id, "set_product_active", "product", product_id,
                                      {"active": active})
                    return True
        return False

    async def set_product_price(
        self,
        product_id: int,
        duration_months: int,
        currency: str,
        amount: Decimal,
        admin_id: int,
    ) -> None:
        value = money(amount)
        if (
            value <= 0
            or currency not in {"DZD", "USD"}
            or duration_months not in {1, 3, 6, 12}
            or (currency == "DZD" and value != value.quantize(Decimal("1")))
        ):
            raise StoreError("invalid_price")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO product_prices
                        (product_id, duration_months, currency, amount, configured)
                    VALUES ($1, $2, $3, $4, TRUE)
                    ON CONFLICT (product_id, duration_months, currency)
                    DO UPDATE SET amount = EXCLUDED.amount, configured = TRUE, updated_at = NOW()
                    """,
                    product_id,
                    duration_months,
                    currency,
                    value,
                )
                await self._audit(
                    conn, admin_id, "set_product_price", "product_price",
                    f"{product_id}:{duration_months}:{currency}", {"amount": str(value)},
                )

    async def adjust_all_prices(self, currency: str, percent: Decimal, admin_id: int) -> int:
        if currency not in {"DZD", "USD"} or percent <= -100 or percent > 1000:
            raise StoreError("invalid_percent")
        quantum = Decimal("1") if currency == "DZD" else Decimal("0.01")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                prices = await conn.fetch(
                    "SELECT id, amount FROM product_prices WHERE currency = $1 AND configured = TRUE FOR UPDATE",
                    currency,
                )
                factor = Decimal("1") + (percent / Decimal("100"))
                for row in prices:
                    amount = (Decimal(row["amount"]) * factor).quantize(quantum, rounding=ROUND_HALF_UP)
                    if amount <= 0:
                        raise StoreError("invalid_percent")
                    await conn.execute(
                        "UPDATE product_prices SET amount = $2, updated_at = NOW() WHERE id = $1",
                        row["id"],
                        amount,
                    )
                await self._audit(
                    conn, admin_id, "adjust_all_prices", "currency", currency,
                    {"percent": str(percent), "rows": len(prices)},
                )
                return len(prices)

    async def admin_stats(self) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM store_users) AS users,
                    (SELECT COUNT(*) FROM purchase_orders) AS orders,
                    (SELECT COUNT(*) FROM purchase_orders WHERE status = 'active') AS active_subscriptions,
                    (SELECT COUNT(*) FROM topup_requests WHERE status = 'pending') AS pending_topups,
                    (SELECT COUNT(*) FROM purchase_orders WHERE status = 'pending_review') AS pending_orders,
                    (SELECT COUNT(*) FROM support_tickets WHERE status = 'open') AS open_tickets
                """
            )

    async def admin_topups(self, status: str = "pending", limit: int = 15) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT t.*, u.username, u.first_name
                FROM topup_requests t JOIN store_users u ON u.telegram_id = t.user_id
                WHERE t.status = $1 ORDER BY t.created_at DESC LIMIT $2
                """,
                status,
                limit,
            )

    async def topup_detail(self, request_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT t.*, u.username, u.first_name
                FROM topup_requests t JOIN store_users u ON u.telegram_id = t.user_id
                WHERE t.id = $1
                """,
                request_id,
            )

    async def admin_orders(self, statuses: list[str] | None = None, limit: int = 20) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE purchase_orders SET status = 'expired', updated_at = NOW()
                WHERE status = 'active' AND expires_at <= NOW()
                """
            )
            if statuses:
                return await conn.fetch(
                    """
                    SELECT o.*, p.name AS product_name, u.username, u.first_name
                    FROM purchase_orders o
                    JOIN products p ON p.id = o.product_id
                    JOIN store_users u ON u.telegram_id = o.user_id
                    WHERE o.status = ANY($1::text[])
                    ORDER BY o.created_at DESC LIMIT $2
                    """,
                    statuses,
                    limit,
                )
            return await conn.fetch(
                """
                SELECT o.*, p.name AS product_name, u.username, u.first_name
                FROM purchase_orders o
                JOIN products p ON p.id = o.product_id
                JOIN store_users u ON u.telegram_id = o.user_id
                ORDER BY o.created_at DESC LIMIT $1
                """,
                limit,
            )

    async def order_detail(self, order_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT o.*, p.name AS product_name, u.username, u.first_name
                FROM purchase_orders o
                JOIN products p ON p.id = o.product_id
                JOIN store_users u ON u.telegram_id = o.user_id
                WHERE o.id = $1
                """,
                order_id,
            )

    async def admin_subscriptions(self, limit: int = 20) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE purchase_orders SET status = 'expired', updated_at = NOW()
                WHERE status = 'active' AND expires_at <= NOW()
                """
            )
            return await conn.fetch(
                """
                SELECT s.*, o.order_code, o.status, p.name AS product_name, u.username, u.first_name
                FROM subscriptions s
                JOIN purchase_orders o ON o.id = s.order_id
                JOIN products p ON p.id = s.product_id
                JOIN store_users u ON u.telegram_id = s.user_id
                ORDER BY s.created_at DESC LIMIT $1
                """,
                limit,
            )

    async def find_user(self, telegram_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT u.telegram_id, u.username, u.first_name, u.language, u.created_at,
                       COALESCE(d.balance, 0) AS dzd_balance,
                       COALESCE(x.balance, 0) AS usd_balance
                FROM store_users u
                LEFT JOIN wallets d ON d.user_id = u.telegram_id AND d.currency = 'DZD'
                LEFT JOIN wallets x ON x.user_id = u.telegram_id AND x.currency = 'USD'
                WHERE u.telegram_id = $1
                """,
                telegram_id,
            )

    async def adjust_wallet(
        self, telegram_id: int, currency: str, amount: Decimal, reason: str, admin_id: int
    ) -> bool:
        delta = money(amount)
        if (
            currency not in {"DZD", "USD"}
            or delta == 0
            or (currency == "DZD" and delta != delta.quantize(Decimal("1")))
        ):
            raise StoreError("invalid_adjustment")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                wallet = await conn.fetchrow(
                    "SELECT balance FROM wallets WHERE user_id = $1 AND currency = $2 FOR UPDATE",
                    telegram_id,
                    currency,
                )
                if not wallet or money(wallet["balance"]) + delta < 0:
                    return False
                await conn.execute(
                    """
                    UPDATE wallets SET balance = balance + $3, updated_at = NOW()
                    WHERE user_id = $1 AND currency = $2
                    """,
                    telegram_id,
                    currency,
                    delta,
                )
                reference = code("ADJ")
                await conn.execute(
                    """
                    INSERT INTO wallet_ledger
                        (user_id, currency, amount, transaction_type, reference, details)
                    VALUES ($1, $2, $3, 'admin_adjustment', $4, $5::jsonb)
                    """,
                    telegram_id,
                    currency,
                    delta,
                    reference,
                    json.dumps({"reason": reason, "admin_id": admin_id}),
                )
                await self._audit(
                    conn, admin_id, "adjust_wallet", "user", telegram_id,
                    {"currency": currency, "amount": str(delta), "reason": reason},
                )
                return True

    async def open_tickets(self, limit: int = 15) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT t.*, u.username, u.first_name,
                       (SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS messages
                FROM support_tickets t JOIN store_users u ON u.telegram_id = t.user_id
                WHERE t.status = 'open' ORDER BY t.updated_at DESC LIMIT $1
                """,
                limit,
            )

    async def ticket_detail(self, ticket_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT t.*, u.username, u.first_name
                FROM support_tickets t JOIN store_users u ON u.telegram_id = t.user_id
                WHERE t.id = $1
                """,
                ticket_id,
            )

    async def ticket_messages(self, ticket_id: int, limit: int = 10) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM support_messages WHERE ticket_id = $1 ORDER BY created_at DESC LIMIT $2
                """,
                ticket_id,
                limit,
            )

    async def available_prices(self, product_id: int, duration_months: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT pp.currency, pp.amount
                FROM product_prices pp
                JOIN products p ON p.id = pp.product_id
                WHERE pp.product_id = $1 AND pp.duration_months = $2
                  AND pp.configured = TRUE AND pp.amount > 0 AND p.active = TRUE
                ORDER BY pp.currency
                """,
                product_id,
                duration_months,
            )

    async def create_checkout_draft(
        self, telegram_id: int, product_id: int, duration_months: int, currency: str
    ) -> asyncpg.Record:
        draft_id = uuid.uuid4()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                maintenance = await conn.fetchval("SELECT value = 'true' FROM app_settings WHERE key = 'maintenance'")
                if maintenance:
                    raise StoreError("maintenance")
                product = await conn.fetchrow(
                    """
                    SELECT p.name, p.active, pp.amount, pp.configured
                    FROM products p
                    JOIN product_prices pp ON pp.product_id = p.id
                    WHERE p.id = $1 AND pp.duration_months = $2 AND pp.currency = $3
                    FOR SHARE OF p, pp
                    """,
                    product_id,
                    duration_months,
                    currency,
                )
                if not product or not product["active"] or not product["configured"] or product["amount"] <= 0:
                    raise StoreError("unavailable")
                existing = await conn.fetchrow(
                    """
                    SELECT d.*, p.name AS product_name
                    FROM checkout_drafts d JOIN products p ON p.id = d.product_id
                    WHERE d.user_id = $1 AND d.product_id = $2
                      AND d.duration_months = $3 AND d.currency = $4 AND d.status = 'open'
                    ORDER BY d.created_at DESC LIMIT 1
                    FOR UPDATE OF d
                    """,
                    telegram_id,
                    product_id,
                    duration_months,
                    currency,
                )
                if existing:
                    return existing
                inserted_id = await conn.fetchval(
                    """
                    INSERT INTO checkout_drafts
                        (id, idempotency_key, user_id, product_id, duration_months, currency, price)
                    VALUES ($1, $1, $2, $3, $4, $5, $6)
                    ON CONFLICT (user_id, product_id, duration_months, currency)
                        WHERE status = 'open' DO NOTHING
                    RETURNING id
                    """,
                    draft_id,
                    telegram_id,
                    product_id,
                    duration_months,
                    currency,
                    product["amount"],
                )
                if inserted_id is None:
                    return await conn.fetchrow(
                        """
                        SELECT d.*, p.name AS product_name
                        FROM checkout_drafts d JOIN products p ON p.id = d.product_id
                        WHERE d.user_id = $1 AND d.product_id = $2
                          AND d.duration_months = $3 AND d.currency = $4 AND d.status = 'open'
                        """,
                        telegram_id,
                        product_id,
                        duration_months,
                        currency,
                    )
                return await conn.fetchrow(
                    """
                    SELECT d.*, p.name AS product_name
                    FROM checkout_drafts d JOIN products p ON p.id = d.product_id
                    WHERE d.id = $1
                    """,
                    draft_id,
                )

    async def confirm_checkout(self, telegram_id: int, draft_id: uuid.UUID) -> tuple[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                draft = await conn.fetchrow(
                    """
                    SELECT d.*, p.name AS product_name
                    FROM checkout_drafts d JOIN products p ON p.id = d.product_id
                    WHERE d.id = $1 AND d.user_id = $2 FOR UPDATE OF d
                    """,
                    draft_id,
                    telegram_id,
                )
                if not draft:
                    raise StoreError("draft_not_found")
                if draft["status"] == "completed":
                    order = await conn.fetchrow(
                        "SELECT * FROM purchase_orders WHERE idempotency_key = $1",
                        draft["idempotency_key"],
                    )
                    if order:
                        return "duplicate", order
                    raise StoreError("order_missing")
                maintenance = await conn.fetchval("SELECT value = 'true' FROM app_settings WHERE key = 'maintenance'")
                if maintenance:
                    raise StoreError("maintenance")
                product_active = await conn.fetchval("SELECT active FROM products WHERE id = $1", draft["product_id"])
                if not product_active:
                    raise StoreError("unavailable")
                wallet = await conn.fetchrow(
                    "SELECT balance FROM wallets WHERE user_id = $1 AND currency = $2 FOR UPDATE",
                    telegram_id,
                    draft["currency"],
                )
                balance = money(wallet["balance"] if wallet else 0)
                price = money(draft["price"])
                if balance < price:
                    return "insufficient", {"shortage": money(price - balance), "currency": draft["currency"]}

                order_code = code("GSO")
                order = await conn.fetchrow(
                    """
                    INSERT INTO purchase_orders
                        (order_code, idempotency_key, user_id, product_id, duration_months, currency, price)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING *
                    """,
                    order_code,
                    draft["idempotency_key"],
                    telegram_id,
                    draft["product_id"],
                    draft["duration_months"],
                    draft["currency"],
                    price,
                )
                await conn.execute(
                    """
                    UPDATE wallets SET balance = balance - $3, updated_at = NOW()
                    WHERE user_id = $1 AND currency = $2
                    """,
                    telegram_id,
                    draft["currency"],
                    price,
                )
                await conn.execute(
                    """
                    INSERT INTO wallet_ledger (user_id, currency, amount, transaction_type, reference, details)
                    VALUES ($1, $2, $3, 'purchase', $4, $5::jsonb)
                    ON CONFLICT (transaction_type, reference, user_id) DO NOTHING
                    """,
                    telegram_id,
                    draft["currency"],
                    -price,
                    str(order["id"]),
                    json.dumps({"order_code": order_code}),
                )
                await conn.execute(
                    "UPDATE checkout_drafts SET status = 'completed', order_id = $2 WHERE id = $1",
                    draft_id,
                    order["id"],
                )
                return "created", order

    async def create_topup(
        self,
        telegram_id: int,
        currency: str,
        amount: Decimal,
        receipt_file_id: str | None = None,
        txid: str | None = None,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            request = await conn.fetchrow(
                """
                INSERT INTO topup_requests
                    (request_code, user_id, currency, amount, receipt_file_id, txid)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                code("GST"),
                telegram_id,
                currency,
                money(amount),
                receipt_file_id,
                txid,
            )
        return request

    async def decide_topup(
        self, request_id: int, admin_id: int, approve: bool, reason: str | None = None
    ) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                request = await conn.fetchrow(
                    """
                    UPDATE topup_requests
                    SET status = $2, rejection_reason = $3, reviewed_by = $4, reviewed_at = NOW()
                    WHERE id = $1 AND status = 'pending'
                    RETURNING *
                    """,
                    request_id,
                    "approved" if approve else "rejected",
                    reason,
                    admin_id,
                )
                if not request:
                    return None
                if approve:
                    await conn.execute(
                        """
                        UPDATE wallets SET balance = balance + $3, updated_at = NOW()
                        WHERE user_id = $1 AND currency = $2
                        """,
                        request["user_id"],
                        request["currency"],
                        request["amount"],
                    )
                    await conn.execute(
                        """
                        INSERT INTO wallet_ledger (user_id, currency, amount, transaction_type, reference, details)
                        VALUES ($1, $2, $3, 'topup', $4, $5::jsonb)
                        ON CONFLICT (transaction_type, reference, user_id) DO NOTHING
                        """,
                        request["user_id"],
                        request["currency"],
                        request["amount"],
                        str(request_id),
                        json.dumps({"request_code": request["request_code"], "reviewed_by": admin_id}),
                    )
                await self._audit(conn, admin_id, "approve_topup" if approve else "reject_topup",
                                  "topup", request_id, {"reason": reason} if reason else {})
                return request

    async def approve_order(self, order_id: int, admin_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    UPDATE purchase_orders SET status = 'approved_waiting_invite', updated_at = NOW()
                    WHERE id = $1 AND status = 'pending_review' RETURNING *
                    """,
                    order_id,
                )
                if order:
                    await self._audit(conn, admin_id, "approve_order", "order", order_id, {})
                return order

    async def store_invite(self, order_id: int, admin_id: int, invite_url: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    UPDATE purchase_orders SET invite_url = $2, updated_at = NOW()
                    WHERE id = $1 AND status = 'approved_waiting_invite'
                    RETURNING *
                    """,
                    order_id,
                    invite_url,
                )
                if order:
                    await self._audit(conn, admin_id, "store_invite_link", "order", order_id, {})
                return order

    async def mark_invite_sent(self, order_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE purchase_orders SET status = 'link_sent', updated_at = NOW()
                WHERE id = $1 AND status = 'approved_waiting_invite' AND invite_url IS NOT NULL
                """,
                order_id,
            )
        return result.endswith("1")

    async def activate_order(self, order_id: int, admin_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM purchase_orders WHERE id = $1 FOR UPDATE",
                    order_id,
                )
                if not order or order["status"] != "link_sent" or not order["invite_url"]:
                    return None
                started = datetime.now(timezone.utc)
                expires = add_months(started, order["duration_months"])
                await conn.execute(
                    """
                    INSERT INTO subscriptions (order_id, user_id, product_id, duration_months, starts_at, ends_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (order_id) DO NOTHING
                    """,
                    order_id,
                    order["user_id"],
                    order["product_id"],
                    order["duration_months"],
                    started,
                    expires,
                )
                updated = await conn.fetchrow(
                    """
                    UPDATE purchase_orders
                    SET status = 'active', joined_at = $2, expires_at = $3, updated_at = NOW()
                    WHERE id = $1 AND status = 'link_sent'
                    RETURNING *
                    """,
                    order_id,
                    started,
                    expires,
                )
                if updated:
                    await self._audit(conn, admin_id, "confirm_subscription_join", "order", order_id,
                                      {"joined_at": started.isoformat(), "expires_at": expires.isoformat()})
                return updated

    async def user_orders(self, telegram_id: int, limit: int = 10) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE purchase_orders
                SET status = 'expired', updated_at = NOW()
                WHERE user_id = $1 AND status = 'active' AND expires_at <= NOW()
                """,
                telegram_id,
            )
            return await conn.fetch(
                """
                SELECT o.*, p.name AS product_name
                FROM purchase_orders o JOIN products p ON p.id = o.product_id
                WHERE o.user_id = $1 ORDER BY o.created_at DESC LIMIT $2
                """,
                telegram_id,
                limit,
            )

    async def setting(self, key: str) -> str:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key) or ""

    async def set_setting(self, key: str, value: str, admin_id: int) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value) VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    key,
                    value,
                )
                await self._audit(conn, admin_id, "update_setting", "setting", key, {})

    async def set_session(
        self, telegram_id: int, kind: str, state: str, payload: dict[str, Any] | None = None
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_sessions (telegram_id, session_kind, state, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (telegram_id, session_kind) DO UPDATE
                SET state = EXCLUDED.state, payload = EXCLUDED.payload, updated_at = NOW()
                """,
                telegram_id,
                kind,
                state,
                json.dumps(payload or {}),
            )

    async def get_session(self, telegram_id: int, kind: str) -> tuple[str, dict[str, Any]] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state, payload FROM bot_sessions WHERE telegram_id = $1 AND session_kind = $2",
                telegram_id,
                kind,
            )
        if not row:
            return None
        payload = row["payload"]
        return row["state"], payload if isinstance(payload, dict) else json.loads(payload)

    async def clear_session(self, telegram_id: int, kind: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM bot_sessions WHERE telegram_id = $1 AND session_kind = $2",
                telegram_id,
                kind,
            )

    async def create_support_ticket(self, telegram_id: int) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO support_tickets (ticket_code, user_id)
                VALUES ($1, $2) RETURNING *
                """,
                code("SUP"),
                telegram_id,
            )

    async def add_support_message(
        self,
        ticket_id: int,
        sender_id: int,
        role: str,
        text: str | None,
        photo_file_id: str | None,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                message = await conn.fetchrow(
                    """
                    INSERT INTO support_messages (ticket_id, sender_id, sender_role, text, photo_file_id)
                    VALUES ($1, $2, $3, $4, $5) RETURNING *
                    """,
                    ticket_id,
                    sender_id,
                    role,
                    text,
                    photo_file_id,
                )
                await conn.execute("UPDATE support_tickets SET updated_at = NOW() WHERE id = $1", ticket_id)
                if role == "admin":
                    await self._audit(
                        conn, sender_id, "reply_support_ticket", "ticket", ticket_id, {}
                    )
                return message

    async def close_ticket(self, ticket_id: int, admin_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    "UPDATE support_tickets SET status = 'closed', updated_at = NOW() WHERE id = $1 AND status = 'open'",
                    ticket_id,
                )
                if result.endswith("1"):
                    await self._audit(conn, admin_id, "close_support_ticket", "ticket", ticket_id, {})
                    return True
        return False

    async def _audit(
        self,
        conn: asyncpg.Connection,
        admin_id: int,
        action: str,
        target_type: str,
        target_id: int | str,
        details: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO admin_audit (admin_id, action, target_type, target_id, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            admin_id,
            action,
            target_type,
            str(target_id),
            json.dumps(details),
        )


def migration_sql() -> str:
    return (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
