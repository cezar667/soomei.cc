"""
Core utilities shared across the Soomei API.

This package will host:
- configuration helpers (env vars, paths, feature flags)
- cross-cutting services such as logging, email/mailer adapters,
  cache abstractions, rate limit helpers, etc.

As modules are migrated out of the monolithic app.py they should depend on
core primitives instead of importing directly from FastAPI or storage layers.
"""

