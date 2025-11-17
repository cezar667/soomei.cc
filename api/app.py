import os
import hashlib
import pathlib
import shutil
import urllib.parse as urlparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.core.config import get_settings
from api.routers import auth as auth_router
from api.routers import cards as cards_router
from api.routers import custom_domain as custom_domain_router
from api.routers import hooks as hooks_router
from api.routers import pages as pages_router
from api.routers import slug as slug_router
from api.services.slug_service import SlugService
from api.services.card_display import configure_public_base

app = FastAPI(title="Soomei Card API v2")

BASE = os.path.dirname(__file__)
WEB = os.path.join(BASE, "..", "web")


class CachedStaticFiles(StaticFiles):
    def set_headers(self, scope, resp, path, stat_result):
        # Cache forte para assets versionados por fingerprint
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"


app.mount("/static", CachedStaticFiles(directory=WEB), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "..", "templates"))

settings = get_settings()
PUBLIC_BASE = settings.public_base_url
configure_public_base(PUBLIC_BASE)
PUBLIC_VERSION = os.getenv("PUBLIC_VERSION")
UPLOADS = os.path.join(WEB, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
PUBLIC_BASE_HOST = (urlparse.urlparse(PUBLIC_BASE).hostname or "").lower()

def _fingerprint_asset(rel_path: str) -> str:
    """
    Gera cópia com hash curto no nome: "card.css" -> "card.<hash8>.css".
    Retorna o nome do arquivo versionado (sem /static).
    """
    src = pathlib.Path(WEB) / rel_path
    if not src.exists():
        # Fallback: retorna o próprio nome sem hash
        return rel_path.replace("\\", "/")
    data = src.read_bytes()
    h = hashlib.sha1(data).hexdigest()[:8]
    stem = src.stem
    suffix = src.suffix  # ex: ".css"
    dst_name = f"{stem}.{h}{suffix}"
    dst = src.with_name(dst_name)
    if not dst.exists():
        shutil.copy2(src, dst)
    return dst_name

# Prepara href do CSS principal
try:
    _css_fp = _fingerprint_asset("card.css")
except Exception:
    _css_fp = "card.css"
CSS_HREF = f"/static/{_css_fp}"
app.state.css_href = CSS_HREF
cards_router.set_css_href(CSS_HREF)
app.state.templates = templates

slug_service = SlugService()
app.state.slug_service = slug_service

app.include_router(auth_router.router)
app.include_router(slug_router.router)
app.include_router(custom_domain_router.router)
app.include_router(hooks_router.router)
app.include_router(pages_router.router)
app.include_router(cards_router.router)


@app.get("/favicon.ico")
def favicon():
    ico_path = os.path.join(WEB, "favicon.ico")
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    png_fallback = os.path.join(WEB, "img", "user01.png")
    if os.path.exists(png_fallback):
        return FileResponse(png_fallback, media_type="image/png")
    return Response(status_code=204)

def _brand_footer_inject(html_doc: str) -> str:
    snippet = (
        "\n    <div class='edit-footer'>\n"
        "        {footer_action_html}\n"
        "      </div>\n  "
    )
    return html_doc.replace("</main>", snippet + "</main>", 1) if "</main>" in html_doc else (html_doc + snippet)


cards_router.set_brand_footer(_brand_footer_inject)
cards_router.configure_environment(
    settings=settings,
    public_base=PUBLIC_BASE,
    public_base_host=PUBLIC_BASE_HOST,
    uploads_dir=UPLOADS,
)
pages_router.configure_pages(
    css_href=CSS_HREF,
    brand_footer=_brand_footer_inject,
    legal_terms_path=os.path.join(BASE, "..", "legal", "terms_v1.md"),
)





