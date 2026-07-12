from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from api.core import csrf
from api.routers import card_edit


class _Repo:
    saved_profile: dict | None = None

    def get_profile(self, _email: str) -> dict:
        return {
            "full_name": "Cezar",
            "title": "Soomei",
            "whatsapp": "5534999999999",
            "links": [],
        }

    def upsert_profile(self, _email: str, profile: dict) -> None:
        self.saved_profile = profile


class _PhotoUpload:
    filename = "foto.jpg"
    content_type = "image/jpeg"

    async def read(self) -> bytes:
        return b"\xff\xd8\xffimage-data"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/edit/cezar",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"cookie", b"session=session-secret"),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )


def _photo_json_request(token: str) -> Request:
    body = json.dumps(
        {
            "content_type": "image/jpeg",
            "filename": "foto.jpg",
            "data_url": "data:image/jpeg;base64,"
            + base64.b64encode(b"\xff\xd8\xffimage-data").decode("ascii"),
        }
    ).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/edit/cezar/photo",
            "query_string": f"csrf_token={token}".encode("ascii"),
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"cookie", b"session=session-secret"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


def test_editor_renders_session_csrf_and_valid_javascript(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", _Repo())
    monkeypatch.setattr(card_edit, "SETTINGS", SimpleNamespace(custom_domains_enabled=False))
    monkeypatch.setattr(card_edit, "BRAND_FOOTER", lambda value: value)

    response = card_edit.edit_card("cezar", request)
    body = response.body.decode("utf-8")
    expected = csrf.ensure_csrf_token(request)

    assert f"name='csrf_token' value='{expected}'" in body
    assert f"action='/edit/cezar?csrf_token={expected}'" in body
    assert "class='edit-form'" in body
    assert "Central do cartão" in body
    assert "name='featured_icon'" in body
    assert "<option value='calendar' selected>Agenda</option>" in body
    assert "hexToRgba" in body
    assert "id='featuredColorReset' onclick=" in body
    assert "id='togglePassword' aria-expanded='false' onclick=" in body
    assert "data-switch-label" in body
    assert "l.textContent=this.checked?&#x27;Exibindo&#x27;:&#x27;Oculto&#x27;" in body
    assert "id='addSlug' class='btn ghost'" in body
    assert "href='/slug/select/tksc4o?next=edit'" in body
    assert "window.soomeiOpenSlugModal" in body
    assert "featuredColor.dispatchEvent(new Event('input'" in body
    assert "style.setProperty('background', 'linear-gradient(135deg,#4f8cff,#73d6ff)', 'important')" in body
    assert "Cache-Control" in response.headers
    assert "csrf_token=" in response.headers.get("set-cookie", "")

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required only for the rendered JavaScript syntax check")
    scripts = re.findall(r"<script>(.*?)</script>", body, flags=re.DOTALL)
    assert scripts
    for script in scripts:
        result = subprocess.run([node, "--check"], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_photo_only_submission_preserves_profile_fields(monkeypatch):
    request = _request()
    repo = _Repo()
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", repo)
    monkeypatch.setattr(
        card_edit,
        "_save_resized_image",
        lambda _data, filename, _size: f"/static/uploads/{filename}?v=new",
    )
    token = csrf.ensure_csrf_token(request)

    response = asyncio.run(
        card_edit.save_edit(
            "cezar",
            request,
            full_name="",
            title="",
            whatsapp="",
            email_public="",
            photo=_PhotoUpload(),
            csrf_token=token,
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cezar"
    assert repo.saved_profile == {
        "full_name": "Cezar",
        "title": "Soomei",
        "whatsapp": "5534999999999",
        "links": [],
        "photo_url": "/static/uploads/tksc4o.jpg?v=new",
    }


def test_empty_submission_with_existing_complete_profile_redirects_without_error(monkeypatch):
    request = _request()
    repo = _Repo()
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", repo)
    token = csrf.ensure_csrf_token(request)

    response = asyncio.run(
        card_edit.save_edit(
            "cezar",
            request,
            full_name="",
            title="",
            whatsapp="",
            email_public="",
            photo=None,
            cover=None,
            portfolio1=None,
            portfolio2=None,
            portfolio3=None,
            portfolio4=None,
            portfolio5=None,
            csrf_token=token,
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cezar"
    assert repo.saved_profile is None


def test_hidden_photo_data_url_submission_updates_photo(monkeypatch):
    request = _request()
    repo = _Repo()
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", repo)
    monkeypatch.setattr(
        card_edit,
        "_save_resized_image",
        lambda _data, filename, _size: f"/static/uploads/{filename}?v=hidden",
    )
    token = csrf.ensure_csrf_token(request)
    data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffimage-data").decode("ascii")

    response = asyncio.run(
        card_edit.save_edit(
            "cezar",
            request,
            full_name="",
            title="",
            whatsapp="",
            email_public="",
            photo=None,
            cover=None,
            portfolio1=None,
            portfolio2=None,
            portfolio3=None,
            portfolio4=None,
            portfolio5=None,
            photo_data_url=data_url,
            csrf_token=token,
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cezar"
    assert repo.saved_profile == {
        "full_name": "Cezar",
        "title": "Soomei",
        "whatsapp": "5534999999999",
        "links": [],
        "photo_url": "/static/uploads/tksc4o.jpg?v=hidden",
    }


def test_hidden_cover_data_url_submission_updates_cover(monkeypatch):
    request = _request()
    repo = _Repo()
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", repo)
    monkeypatch.setattr(
        card_edit,
        "_save_resized_image",
        lambda _data, filename, _size: f"/static/uploads/{filename}?v=cover",
    )
    token = csrf.ensure_csrf_token(request)
    data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffimage-data").decode("ascii")

    response = asyncio.run(
        card_edit.save_edit(
            "cezar",
            request,
            full_name="Cezar",
            title="Soomei",
            whatsapp="5534999999999",
            email_public="",
            site_url="",
            address="",
            google_review_url="",
            google_review_show="",
            featured_label="",
            featured_url="",
            featured_icon="briefcase",
            featured_color="#FFB473",
            featured_enabled="",
            label1="",
            href1="",
            label2="",
            href2="",
            label3="",
            href3="",
            label4="",
            href4="",
            theme_color="",
            pix_key="",
            current_password="",
            new_password="",
            confirm_password="",
            password_mode="0",
            cover_remove="0",
            portfolio_enabled="",
            portfolio_remove1="0",
            portfolio_remove2="0",
            portfolio_remove3="0",
            portfolio_remove4="0",
            portfolio_remove5="0",
            photo=None,
            cover=None,
            portfolio1=None,
            portfolio2=None,
            portfolio3=None,
            portfolio4=None,
            portfolio5=None,
            cover_data_url=data_url,
            csrf_token=token,
        )
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cezar"
    assert repo.saved_profile == {
        "full_name": "Cezar",
        "title": "Soomei",
        "whatsapp": "5534999999999",
        "email_public": "",
        "site_url": "",
        "address": "",
        "google_review_url": "",
        "google_review_show": False,
        "featured_color": "#FFB473",
        "featured_label": "",
        "featured_url": "",
        "featured_enabled": False,
        "featured_icon": "briefcase",
        "pix_key": "",
        "theme_color": "#000000",
        "links": [],
        "portfolio_images": [],
        "portfolio_enabled": False,
        "cover_url": "/static/uploads/tksc4o_cover.jpg?v=cover",
    }


def test_json_photo_upload_avoids_multipart_and_preserves_profile(monkeypatch):
    repo = _Repo()
    token = csrf.ensure_csrf_token(_request())
    request = _photo_json_request(token)
    monkeypatch.setattr(
        card_edit,
        "find_card_by_slug",
        lambda _slug: ({}, "tksc4o", {"user": "owner@example.com"}),
    )
    monkeypatch.setattr(card_edit, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(card_edit, "_sql_repo", repo)
    monkeypatch.setattr(
        card_edit,
        "_save_resized_image",
        lambda _data, filename, _size: f"/static/uploads/{filename}?v=json",
    )

    response = asyncio.run(card_edit.save_profile_photo("cezar", request))

    assert response.status_code == 200
    assert repo.saved_profile == {
        "full_name": "Cezar",
        "title": "Soomei",
        "whatsapp": "5534999999999",
        "links": [],
        "photo_url": "/static/uploads/tksc4o.jpg?v=json",
    }
