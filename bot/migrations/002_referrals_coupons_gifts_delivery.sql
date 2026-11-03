ALTER TABLE store_users
    ADD COLUMN IF NOT EXISTS referred_by BIGINT REFERENCES store_users(telegram_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_store_users_referred_by ON store_users(referred_by);

CREATE TABLE IF NOT EXISTS coupons (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    discount_type TEXT NOT NULL CHECK (discount_type IN ('fixed', 'percent')),
    discount_value NUMERIC(18, 2) NOT NULL CHECK (discount_value > 0),
    currency TEXT CHECK (currency IN ('DZD', 'USD')),
    max_uses INTEGER NOT NULL CHECK (max_uses > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (discount_type = 'fixed' AND currency IS NOT NULL)
        OR (discount_type = 'percent' AND currency IS NULL AND discount_value <= 100)
    )
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id BIGSERIAL PRIMARY KEY,
    coupon_id BIGINT NOT NULL REFERENCES coupons(id) ON DELETE RESTRICT,
    order_id BIGINT NOT NULL UNIQUE REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    user_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    original_amount NUMERIC(18, 2) NOT NULL CHECK (original_amount >= 0),
    discount_amount NUMERIC(18, 2) NOT NULL CHECK (discount_amount >= 0),
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (coupon_id, order_id)
);

ALTER TABLE checkout_drafts
    ADD COLUMN IF NOT EXISTS coupon_id BIGINT REFERENCES coupons(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS coupon_code TEXT,
    ADD COLUMN IF NOT EXISTS coupon_discount NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (coupon_discount >= 0);

ALTER TABLE purchase_orders
    ADD COLUMN IF NOT EXISTS coupon_id BIGINT REFERENCES coupons(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS coupon_code TEXT,
    ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (discount_amount >= 0),
    ADD COLUMN IF NOT EXISTS subtotal_price NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (subtotal_price >= 0),
    ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1
        CHECK (quantity > 0),
    ADD COLUMN IF NOT EXISTS activation_code TEXT,
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;

UPDATE purchase_orders
SET subtotal_price = price
WHERE subtotal_price = 0 AND discount_amount = 0;

ALTER TABLE purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_price_check;
ALTER TABLE purchase_orders
    ADD CONSTRAINT purchase_orders_price_nonnegative_check CHECK (price >= 0);
ALTER TABLE purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_status_check;
ALTER TABLE purchase_orders
    ADD CONSTRAINT purchase_orders_status_check
    CHECK (status IN (
        'pending_review',
        'approved_waiting_invite',
        'link_sent',
        'active',
        'expired',
        'code_preparing',
        'code_delivered',
        'code_delivery_failed'
    ));

CREATE TABLE IF NOT EXISTS referral_rewards (
    id BIGSERIAL PRIMARY KEY,
    referrer_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    referred_user_id BIGINT NOT NULL UNIQUE REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    order_id BIGINT NOT NULL UNIQUE REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount IN (30, 50)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (referrer_id <> referred_user_id)
);

CREATE TABLE IF NOT EXISTS gift_drafts (
    id UUID PRIMARY KEY,
    sender_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    receiver_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount >= 150),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'cancelled')),
    gift_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CHECK (sender_id <> receiver_id)
);

CREATE TABLE IF NOT EXISTS gift_transfers (
    id BIGSERIAL PRIMARY KEY,
    gift_code TEXT NOT NULL UNIQUE,
    idempotency_key UUID NOT NULL UNIQUE,
    draft_id UUID NOT NULL UNIQUE REFERENCES gift_drafts(id) ON DELETE RESTRICT,
    sender_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    receiver_id BIGINT NOT NULL REFERENCES store_users(telegram_id) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount >= 150),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (sender_id <> receiver_id)
);

CREATE TABLE IF NOT EXISTS order_notifications (
    order_id BIGINT NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    phase TEXT NOT NULL CHECK (phase IN ('purchase', 'approval', 'code_delivery')),
    state TEXT NOT NULL DEFAULT 'claimed' CHECK (state IN ('claimed', 'sent', 'failed')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    error TEXT,
    PRIMARY KEY (order_id, phase)
);

CREATE INDEX IF NOT EXISTS idx_coupons_active_expiry ON coupons(active, expires_at);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON coupon_redemptions(coupon_id, redeemed_at);
CREATE INDEX IF NOT EXISTS idx_gift_transfers_sender ON gift_transfers(sender_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gift_transfers_receiver ON gift_transfers(receiver_id, created_at DESC);
