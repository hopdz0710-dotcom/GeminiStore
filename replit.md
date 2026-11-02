# Gemini Store V1

Telegram storefront for Gemini Plus and Gemini Pro with admin-reviewed wallet top-ups, purchase fulfillment, support, and PostgreSQL persistence.

## Run & Operate

- `python -m bot.migrate` — explicitly apply the idempotent bot schema migration
- `python -m bot` — run the Telegram long-polling bot
- `python -m unittest -v tests.test_core_flows` — run PostgreSQL-backed core flow checks
- Required Replit Secrets: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS` (comma-separated numeric Telegram IDs)
- `DATABASE_URL` is provided by the Replit-managed PostgreSQL database; do not set it manually.
- The pre-existing API and mockup artifacts are unrelated template services.

## Stack

- Python 3.13, aiogram 3, asyncpg
- PostgreSQL with explicit idempotent schema migration
- Existing workspace tools: pnpm, Node.js 24, TypeScript 5.9

## Where things live

- `bot/` — bot configuration, data access, user/admin handlers, and migration.
- `bot/migrations/001_initial.sql` — schema and initial products/reference DZD prices.
- `tests/test_core_flows.py` — PostgreSQL-backed tests that run in a temporary schema and remove it after each run.
- `README.md` — operator setup and payment/fulfillment notes.

## Architecture decisions

- Payment proofs are manually reviewed; a receipt alone never credits a wallet.
- Products start disabled and must stay off until an administrator verifies real fulfillment capacity.
- USD prices are independent and unavailable until an administrator configures them.
- Invitation delivery and membership confirmation are separate; subscription time starts only after an administrator confirms the join.
- Wallet changes, top-up decisions, purchase debits, and subscription activation use PostgreSQL transactions and administrative audit records.
- The bot checks that the schema exists at startup; migrations are run explicitly, not as startup DDL.

## Product

- Arabic and English menus, one-time signup credit, DZD/USD wallets, product ordering, manual top-up review, order fulfillment, subscriptions, and support.
- `/admin` opens the protected Telegram admin panel for IDs listed in `ADMIN_IDS`.

## User preferences

_No additional standing preferences recorded._

## Gotchas

- Apply the schema with `python -m bot.migrate` before starting the bot.
- Sending an invitation link does not make the subscription active; an administrator must confirm actual membership.
- Admins must start a private chat with the bot before it can send them notifications.

## Pointers

- Keep the bot secret and admin IDs in Replit Secrets; do not copy them into files.
