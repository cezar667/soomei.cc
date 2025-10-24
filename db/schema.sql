-- db/schema.sql — Postgres/D1
CREATE TABLE members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_ref TEXT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  whatsapp TEXT,
  cnpj TEXT,
  status TEXT NOT NULL DEFAULT 'ativo',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE cards (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  member_id UUID REFERENCES members(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'ativo',
  target_default TEXT NOT NULL DEFAULT '/u/{slug}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE taps (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT NOT NULL,
  ip INET,
  user_agent TEXT,
  referrer TEXT,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cards_slug ON cards(slug);
CREATE INDEX idx_taps_slug_ts ON taps(slug, ts DESC);
