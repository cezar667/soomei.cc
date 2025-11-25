"""Entry points for public and admin FastAPI apps."""
from api.app import app as public_app, create_app
from api.admin_app import app as admin_app, create_admin_app

__all__ = ["public_app", "admin_app", "create_app", "create_admin_app"]
