-- Membership platform webhook integration.
-- Reference SQL for PostgreSQL deployments. The current dev flow can also
-- create these objects through SQLAlchemy Base.metadata.create_all().

ALTER TABLE cards
    ADD COLUMN IF NOT EXISTS status_reason VARCHAR(60),
    ADD COLUMN IF NOT EXISTS external_provider VARCHAR(50),
    ADD COLUMN IF NOT EXISTS external_subscription_id VARCHAR(150),
    ADD COLUMN IF NOT EXISTS external_product_id VARCHAR(150);

CREATE UNIQUE INDEX IF NOT EXISTS uk_cards_external_subscription_product
ON cards(external_provider, external_subscription_id, external_product_id)
WHERE external_provider IS NOT NULL
  AND external_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cards_external_provider
ON cards(external_provider);

CREATE INDEX IF NOT EXISTS idx_cards_external_subscription
ON cards(external_subscription_id);

CREATE TABLE IF NOT EXISTS members (
    id VARCHAR(36) PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    external_customer_id VARCHAR(150) NOT NULL,
    email VARCHAR(255),
    name VARCHAR(255),
    phone VARCHAR(40),
    document VARCHAR(40),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_members_provider_customer UNIQUE (provider, external_customer_id)
);

CREATE INDEX IF NOT EXISTS idx_members_provider
ON members(provider);

CREATE INDEX IF NOT EXISTS idx_members_email
ON members(email);

CREATE TABLE IF NOT EXISTS external_subscriptions (
    id VARCHAR(36) PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    external_customer_id VARCHAR(150) NOT NULL,
    external_subscription_id VARCHAR(150),
    external_order_id VARCHAR(150),
    external_product_id VARCHAR(150),
    external_plan_id VARCHAR(150),
    member_id VARCHAR(36) REFERENCES members(id) ON DELETE SET NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_external_subscriptions_customer UNIQUE (provider, external_customer_id),
    CONSTRAINT uk_external_subscriptions_subscription UNIQUE (provider, external_subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_external_subscriptions_customer
ON external_subscriptions(provider, external_customer_id);

CREATE INDEX IF NOT EXISTS idx_external_subscriptions_subscription
ON external_subscriptions(provider, external_subscription_id);

CREATE INDEX IF NOT EXISTS idx_external_subscriptions_member_id
ON external_subscriptions(member_id);

CREATE TABLE IF NOT EXISTS webhook_events (
    id VARCHAR(36) PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    external_event_id VARCHAR(150) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(30) NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    correlation_id VARCHAR(80),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_started_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    error_code VARCHAR(100),
    error_message TEXT,
    CONSTRAINT uk_webhook_event UNIQUE (provider, external_event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status_received_at
ON webhook_events(status, received_at);

CREATE INDEX IF NOT EXISTS idx_webhook_events_retry
ON webhook_events(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_webhook_events_correlation_id
ON webhook_events(correlation_id);

CREATE TABLE IF NOT EXISTS card_status_history (
    id VARCHAR(36) PRIMARY KEY,
    card_uid VARCHAR(64) NOT NULL REFERENCES cards(uid) ON DELETE CASCADE,
    previous_status VARCHAR(40),
    new_status VARCHAR(40) NOT NULL,
    reason VARCHAR(60) NOT NULL,
    source VARCHAR(40) NOT NULL,
    actor_id VARCHAR(150),
    external_event_id VARCHAR(150),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_card_status_history_card_created
ON card_status_history(card_uid, created_at DESC);

