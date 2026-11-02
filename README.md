# Gemini Store V1

Telegram storefront for Gemini Plus and Gemini Pro, implemented with Python, aiogram 3, and PostgreSQL. Payments are reviewed by an administrator; the bot never treats a receipt as proof of received funds, and subscription activation starts only when an administrator confirms the user's actual join.

## Run

1. Set Replit Secrets `TELEGRAM_BOT_TOKEN` and `ADMIN_IDS`. `ADMIN_IDS` is a comma-separated list of Telegram numeric IDs.
2. The project uses its runtime-managed PostgreSQL `DATABASE_URL`; do not add a second database URL.
3. Apply the initial schema once:

   ```sh
   python -m bot.migrate
   ```

4. Start the long-polling bot:

   ```sh
   python -m bot
   ```

The `Gemini Store Bot` workflow runs the bot. The database migration is intentionally separate so schema changes are explicit and do not run as startup DDL.

Run the core database-flow checks with `python -m unittest -v tests.test_core_flows`. They use and remove a temporary PostgreSQL schema rather than touching store data.

## Admin setup

- Open the bot and send `/start` from each administrator account, then `/admin`.
- Keep products disabled until you have verified real fulfillment capacity; turn each one on from product management only while invitation fulfillment is available.
- Set independent USD product prices under **المنتجات والأسعار**. USD prices are unavailable to buyers until configured.
- Add Binance address, network, coin, and transfer instructions under **إعدادات Binance** before enabling USD top-ups.
- DZD top-ups use the supplied Djezzy Flexy number. Confirm receipt before accepting a top-up.
- Purchase approval prompts for an HTTPS invitation URL. The bot sends it only to that order's buyer. Confirm membership separately to start the subscription duration.

## Safety and persistence

- PostgreSQL transactions protect registration rewards, wallet debits/credits, top-up decisions, and subscription activation from duplicate callbacks.
- Price, order, top-up, wallet ledger, support, user-language, and administrative audit data survive bot restarts.
- Keep bot tokens and administrator IDs in Replit Secrets. Never commit them to source files.
- A Telegram bot cannot verify an external Gemini subscription or whether a user joined a third-party invitation. Administration must verify the actual receipt/join and must only sell plans the store can fulfill.
