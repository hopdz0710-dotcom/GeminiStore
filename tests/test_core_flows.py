from __future__ import annotations

import asyncio
import secrets
import unittest
from decimal import Decimal

import asyncpg

from bot.config import Settings
from bot.db import Database


class CoreFlowChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = Settings.from_env()
        self.database_url = config.database_url
        self.schema_name = f"gemini_store_test_{secrets.token_hex(6)}"
        self.db = None
        self.addAsyncCleanup(self.cleanup)
        bootstrap = await asyncpg.connect(self.database_url, command_timeout=60)
        try:
            await bootstrap.execute(f'CREATE SCHEMA "{self.schema_name}"')
            await bootstrap.execute(f'SET search_path TO "{self.schema_name}"')
            from bot.db import migration_sql
            await bootstrap.execute(migration_sql())
        finally:
            await bootstrap.close()
        pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=3,
            server_settings={"search_path": self.schema_name},
        )
        self.db = Database(pool)
        await self.db.verify_schema()
        self.buyer_id = 8_500_000_000_000_000_000 + secrets.randbelow(100_000_000_000)
        self.low_id = self.buyer_id + 1

    async def cleanup(self) -> None:
        if self.db:
            await self.db.close()
        connection = await asyncpg.connect(self.database_url)
        try:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema_name}" CASCADE')
        finally:
            await connection.close()

    async def test_registration_topup_purchase_fulfillment_and_support(self) -> None:
        language, created = await self.db.register_user(self.buyer_id, "flow_test", "Flow Test")
        self.assertEqual(language, "ar")
        self.assertTrue(created)
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["DZD"], Decimal("20.00"))
        self.assertEqual(
            await self.db.pool.fetchval("SELECT COUNT(*) FROM products WHERE active = TRUE"),
            0,
        )

        _, created_again = await self.db.register_user(self.buyer_id, "flow_test", "Flow Test")
        self.assertFalse(created_again)
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["DZD"], Decimal("20.00"))

        topup = await self.db.create_topup(self.buyer_id, "DZD", Decimal("500"), receipt_file_id="test-receipt")
        with self.assertRaises(asyncpg.UniqueViolationError):
            await self.db.create_topup(
                self.buyer_id, "DZD", Decimal("500"), receipt_file_id="test-receipt"
            )
        accepted = await self.db.decide_topup(topup["id"], self.buyer_id, True)
        self.assertIsNotNone(accepted)
        self.assertIsNone(await self.db.decide_topup(topup["id"], self.buyer_id, True))
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["DZD"], Decimal("520.00"))

        plus_id = await self.db.pool.fetchval("SELECT id FROM products WHERE slug = 'gemini-plus'")
        pro_id = await self.db.pool.fetchval("SELECT id FROM products WHERE slug = 'gemini-pro'")
        await self.db.set_product_active(plus_id, True, self.buyer_id)
        await self.db.set_product_active(pro_id, True, self.buyer_id)
        await self.db.set_product_price(plus_id, 1, "USD", Decimal("5"), self.buyer_id)
        self.assertEqual(await self.db.adjust_all_prices("USD", Decimal("20"), self.buyer_id), 1)
        usd_price = await self.db.pool.fetchval(
            """
            SELECT amount FROM product_prices
            WHERE product_id = $1 AND duration_months = 1 AND currency = 'USD'
            """,
            plus_id,
        )
        dzd_price = await self.db.pool.fetchval(
            """
            SELECT amount FROM product_prices
            WHERE product_id = $1 AND duration_months = 1 AND currency = 'DZD'
            """,
            plus_id,
        )
        self.assertEqual(usd_price, Decimal("6.00"))
        self.assertEqual(dzd_price, Decimal("150.00"))

        draft = await self.db.create_checkout_draft(self.buyer_id, plus_id, 1, "DZD")
        repeated_draft = await self.db.create_checkout_draft(self.buyer_id, plus_id, 1, "DZD")
        self.assertEqual(repeated_draft["id"], draft["id"])
        await self.db.set_product_price(plus_id, 1, "DZD", Decimal("200"), self.buyer_id)
        result, order = await self.db.confirm_checkout(self.buyer_id, draft["id"])
        self.assertEqual(result, "created")
        self.assertEqual(order["price"], Decimal("150.00"))
        duplicate_result, duplicate_order = await self.db.confirm_checkout(self.buyer_id, draft["id"])
        self.assertEqual(duplicate_result, "duplicate")
        self.assertEqual(duplicate_order["id"], order["id"])
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["DZD"], Decimal("370.00"))

        self.assertIsNotNone(await self.db.approve_order(order["id"], self.buyer_id))
        self.assertIsNotNone(
            await self.db.store_invite(order["id"], self.buyer_id, "https://example.com/private-invite")
        )
        self.assertTrue(await self.db.mark_invite_sent(order["id"]))
        activated = await self.db.activate_order(order["id"], self.buyer_id)
        self.assertIsNotNone(activated)
        self.assertGreater(activated["expires_at"], activated["joined_at"])
        self.assertIsNone(await self.db.activate_order(order["id"], self.buyer_id))

        await self.db.register_user(self.low_id, None, "Low Balance")
        low_draft = await self.db.create_checkout_draft(self.low_id, pro_id, 1, "DZD")
        result, shortage = await self.db.confirm_checkout(self.low_id, low_draft["id"])
        self.assertEqual(result, "insufficient")
        self.assertEqual(shortage["shortage"], Decimal("280.00"))
        self.assertEqual((await self.db.wallet_balances(self.low_id))["DZD"], Decimal("20.00"))
        self.assertEqual(await self.db.pool.fetchval(
            "SELECT COUNT(*) FROM purchase_orders WHERE user_id = $1", self.low_id
        ), 0)

        rejected = await self.db.create_topup(self.buyer_id, "USD", Decimal("3"), txid="test-txid")
        self.assertIsNotNone(await self.db.decide_topup(rejected["id"], self.buyer_id, False, "test rejection"))
        self.assertIsNone(await self.db.decide_topup(rejected["id"], self.buyer_id, True))
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["USD"], Decimal("0.00"))

        self.assertTrue(
            await self.db.adjust_wallet(self.buyer_id, "USD", Decimal("2"), "test admin credit", self.buyer_id)
        )
        self.assertFalse(
            await self.db.adjust_wallet(self.buyer_id, "USD", Decimal("-3"), "test overdraw", self.buyer_id)
        )
        self.assertEqual((await self.db.wallet_balances(self.buyer_id))["USD"], Decimal("2.00"))

        ticket = await self.db.create_support_ticket(self.buyer_id)
        await self.db.add_support_message(ticket["id"], self.buyer_id, "user", "test support", None)
        self.assertEqual(len(await self.db.ticket_messages(ticket["id"])), 1)


if __name__ == "__main__":
    unittest.main()
