"""
High-level use cases for the Soomei API.

Each service module should orchestrate repositories/adapters to implement
business rules (activate card, reset password, select slug, etc.).

Routers (FastAPI endpoints) should call these services instead of manipulating
the JSON database or sessions directly.
"""

