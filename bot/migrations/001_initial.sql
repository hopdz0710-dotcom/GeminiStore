CREATE TABLE IF NOT EXISTS store_users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'ar' CHECK (language IN ('ar', 'en')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wallets (
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    balance NUMERIC(18, 2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, currency)
);

CREATE TABLE IF NOT EXISTS wallet_ledger (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    amount NUMERIC(18, 2) NOT NULL,
    transaction_type TEXT NOT NULL,
    reference TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (transaction_type, reference, user_id)
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_prices (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    duration_months INTEGER NOT NULL CHECK (duration_months IN (1, 3, 6, 12)),
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    amount NUMERIC(18, 2) NOT NULL DEFAULT 0 CHECK (amount >= 0),
    configured BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, duration_months, currency)
);

CREATE TABLE IF NOT EXISTS checkout_drafts (
    id UUID PRIMARY KEY,
    idempotency_key UUID NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    duration_months INTEGER NOT NULL CHECK (duration_months IN (1, 3, 6, 12)),
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    price NUMERIC(18, 2) NOT NULL CHECK (price > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed')),
    order_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_checkout_one_open
    ON checkout_drafts(user_id, product_id, duration_months, currency)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS purchase_orders (
    id BIGSERIAL PRIMARY KEY,
    order_code TEXT NOT NULL UNIQUE,
    idempotency_key UUID NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    duration_months INTEGER NOT NULL CHECK (duration_months IN (1, 3, 6, 12)),
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    price NUMERIC(18, 2) NOT NULL CHECK (price > 0),
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved_waiting_invite', 'link_sent', 'active', 'expired')),
    invite_url TEXT,
    joined_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS topup_requests (
    id BIGSERIAL PRIMARY KEY,
    request_code TEXT NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    currency TEXT NOT NULL CHECK (currency IN ('DZD', 'USD')),
    amount NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    receipt_file_id TEXT,
    txid TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    rejection_reason TEXT,
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (receipt_file_id IS NOT NULL OR txid IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL UNIQUE REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    duration_months INTEGER NOT NULL CHECK (duration_months IN (1, 3, 6, 12)),
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id BIGSERIAL PRIMARY KEY,
    ticket_code TEXT NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_messages (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES support_tickets(id) ON DELETE RESTRICT,
    sender_id BIGINT NOT NULL,
    sender_role TEXT NOT NULL CHECK (sender_role IN ('user', 'admin')),
    text TEXT,
    photo_file_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (text IS NOT NULL OR photo_file_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_sessions (
    telegram_id BIGINT NOT NULL,
    session_kind TEXT NOT NULL CHECK (session_kind IN ('user', 'admin')),
    state TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (telegram_id, session_kind)
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topups_status_created ON topup_requests(status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_topup_unique_receipt
    ON topup_requests(receipt_file_id)
    WHERE receipt_file_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_topup_unique_txid
    ON topup_requests(LOWER(txid))
    WHERE txid IS NOT NULL AND txid <> '';
CREATE INDEX IF NOT EXISTS idx_orders_status_created ON purchase_orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON purchase_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON support_tickets(status, created_at DESC);

INSERT INTO products (slug, name) VALUES
    ('gemini-plus', 'Gemini Plus'),
    ('gemini-pro', 'Gemini Pro')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO product_prices (product_id, duration_months, currency, amount, configured)
SELECT p.id, prices.duration_months, prices.currency, prices.amount, prices.configured
FROM products AS p
CROSS JOIN (VALUES
    (1, 'DZD', 150.00, TRUE), (3, 'DZD', 300.00, TRUE),
    (6, 'DZD', 450.00, TRUE), (12, 'DZD', 750.00, TRUE),
    (1, 'USD', 0.00, FALSE), (3, 'USD', 0.00, FALSE),
    (6, 'USD', 0.00, FALSE), (12, 'USD', 0.00, FALSE)
) AS prices(duration_months, currency, amount, configured)
WHERE p.slug = 'gemini-plus'
ON CONFLICT (product_id, duration_months, currency) DO NOTHING;

INSERT INTO product_prices (product_id, duration_months, currency, amount, configured)
SELECT p.id, prices.duration_months, prices.currency, prices.amount, prices.configured
FROM products AS p
CROSS JOIN (VALUES
    (1, 'DZD', 300.00, TRUE), (3, 'DZD', 500.00, TRUE),
    (6, 'DZD', 800.00, TRUE), (12, 'DZD', 1400.00, TRUE),
    (1, 'USD', 0.00, FALSE), (3, 'USD', 0.00, FALSE),
    (6, 'USD', 0.00, FALSE), (12, 'USD', 0.00, FALSE)
) AS prices(duration_months, currency, amount, configured)
WHERE p.slug = 'gemini-pro'
ON CONFLICT (product_id, duration_months, currency) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES
    ('maintenance', 'false'),
    ('binance_address', ''),
    ('binance_network', ''),
    ('binance_currency', ''),
    ('binance_instructions', '')
ON CONFLICT (key) DO NOTHING;
