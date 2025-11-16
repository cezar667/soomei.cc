"""
Utility helpers shared across routers/services.
"""

from urllib.parse import urlparse
from typing import Optional

from .config import get_settings


def absolute_url(path: str, base: Optional[str] = None) -> str:
    """
    Converte caminhos relativos em URLs absolutas usando PUBLIC_BASE.
    """
    settings = get_settings()
    base_url = (base or settings.public_base_url).rstrip("/")
    if not path:
        return base_url + "/"
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base_url + path

