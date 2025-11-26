"""Helpers for card display and public profile routes."""
from __future__ import annotations

import re
import unicodedata
import urllib.parse as urlparse
from typing import Optional

from fastapi import Request

from api.repositories.json_storage import db_defaults, load, save
from api.services.domain_service import active_custom_domain_host

PUBLIC_BASE = ""


def configure_public_base(base_url: str) -> None:
    """Configure PUBLIC_BASE used by helpers that build absolute URLs."""
    global PUBLIC_BASE
    base = (base_url or "").strip()
    PUBLIC_BASE = base.rstrip("/") if base else ""

CPF_RE   = re.compile(r"^\d{11}$")

CNPJ_RE  = re.compile(r"^\d{14}$")

UUID_RE  = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

DEFAULT_AVATAR = "/static/img/user01.png"

FEATURED_DEFAULT_COLOR = "#FFB473"

def normalize_external_url(value: str) -> str:
    """
    Garante que links externos tenham esquema quando o usuário não define (https://).
    Mantém mailto: e tel: intocados.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if re.match(r"^(https?://|mailto:|tel:)", v, re.IGNORECASE):
        return v
    return "https://" + v.lstrip("/")

def _absolute_asset_url(path: str, base: str | None = None) -> str:
    p = (path or "").strip()
    if not p:
        p = DEFAULT_AVATAR
    if p.startswith("http://") or p.startswith("https://"):
        return p
    base_url = (base or PUBLIC_BASE).rstrip("/")
    if p.startswith("/"):
        return f"{base_url}{p}"
    return f"{base_url}/{p.lstrip('/')}"

def _normalize_hex_color(value: str | None, fallback: str = FEATURED_DEFAULT_COLOR) -> str:
    if not value:
        return fallback
    v = value.strip()
    if not v:
        return fallback
    if re.fullmatch(r"#([0-9a-fA-F]{6})", v):
        return v.upper()
    if re.fullmatch(r"[0-9a-fA-F]{6}", v):
        return ("#" + v).upper()
    return fallback

def _hex_to_rgb_tuple(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)

def _mix_hex_color(value: str, factor: float) -> str:
    r, g, b = _hex_to_rgb_tuple(value)
    def _mix(c: int) -> int:
        if factor >= 0:
            return int(c + (255 - c) * min(factor, 1))
        return int(c * (1 + max(factor, -1)))
    return "#{:02X}{:02X}{:02X}".format(_mix(r), _mix(g), _mix(b))

def _rgb_string(value: str) -> str:
    r, g, b = _hex_to_rgb_tuple(value)
    return f"{r},{g},{b}"

def _pick_text_color(value: str) -> str:
    r, g, b = _hex_to_rgb_tuple(value)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#2B1600" if luminance > 140 else "#FFF8F0"

def _tlv(_id: str, value: str) -> str:
    l = f"{len(value):02d}"
    return f"{_id}{l}{value}"

def _crc16_ccitt(data: str) -> str:
    # CRC16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
    crc = 0xFFFF
    for ch in data:
        crc ^= (ord(ch) << 8) & 0xFFFF
        for _ in range(8):
            if (crc & 0x8000):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def _norm_text(s: str, maxlen: int) -> str:
    t = unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9 \-\.]+", "", t).strip() or "NA"
    return t[:maxlen].upper()

def _is_valid_cpf(cpf: str) -> bool:
    cpf = re.sub(r"\D", "", cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    def dv(nums, mult):
        s = sum(int(d) * m for d, m in zip(nums, mult))
        r = (s * 10) % 11
        return 0 if r == 10 else r
    d1 = dv(cpf[:9], range(10, 1, -1))
    d2 = dv(cpf[:9] + str(d1), range(11, 1, -1))
    return cpf[-2:] == f"{d1}{d2}"

def _is_valid_cnpj(cnpj: str) -> bool:
    cnpj = re.sub(r"\D", "", cnpj)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    def calc_dv(nums, pesos):
        s = sum(int(n) * p for n, p in zip(nums, pesos))
        r = s % 11
        return 0 if r < 2 else 11 - r
    p1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    p2 = [6] + p1
    d1 = calc_dv(cnpj[:12], p1)
    d2 = calc_dv(cnpj[:12] + str(d1), p2)
    return cnpj[-2:] == f"{d1}{d2}"

def _normalize_pix_key(pix_key: str) -> str:
    key = (pix_key or "").strip()

    # E-mail
    if "@" in key:
        k = key.lower()
        if len(k) > 77:
            raise ValueError("E-mail da chave Pix excede 77 caracteres.")
        return k

    # EVP (aleatória) comum como UUID
    if UUID_RE.match(key):
        return key

    # Já está em E.164?
    if key.startswith("+") and re.fullmatch(r"\+\d{11,15}", key):
        return key

    digits = re.sub(r"\D", "", key)

    # CPF/CNPJ válidos: retornar exatamente os dígitos
    if _is_valid_cpf(digits):
        return digits
    if _is_valid_cnpj(digits):
        return digits

    # Telefone (heurística): 10?13 dígitos ? normalizar para E.164 assumindo Brasil (+55) quando faltar DDI
    if 10 <= len(digits) <= 13:
        if digits.startswith("55"):
            phone = "+" + digits
        else:
            phone = "+55" + digits
        if not re.fullmatch(r"\+\d{11,15}", phone):
            raise ValueError("Telefone fora do padrão E.164 após normalização.")
        return phone

    # Caso restante: pode ser outra EVP não-UUID; devolver como veio (máx. 77 chars)
    if len(key) <= 77:
        return key
    raise ValueError("Chave Pix inválida ou muito longa (máx. 77 caracteres).")

def _sanitize_txid(txid: str) -> str:
    return (txid or "***").strip()[:25]

def build_pix_emv(pix_key: str, amount: Optional[float], merchant_name: str, merchant_city: str, txid: str = "***") -> str:
    # 00: Payload Format Indicator
    payload = _tlv("00", "01")

    # 01: Point of Initiation Method ? 12 (dinâmico) quando tem valor; 11 (estático) sem valor
    poi = "12" if (amount or 0) > 0 else "11"
    payload += _tlv("01", poi)

    # 26: Merchant Account Information (GUI + chave normalizada)
    normalized_key = _normalize_pix_key(pix_key)
    mai = _tlv("00", "br.gov.bcb.pix") + _tlv("01", normalized_key)
    payload += _tlv("26", mai)

    # 52: MCC (0000), 53: Moeda (986)
    payload += _tlv("52", "0000")
    payload += _tlv("53", "986")

    # 54: Valor (opcional)
    if (amount or 0) > 0:
        payload += _tlv("54", f"{amount:.2f}")

    # 58: País, 59: Nome, 60: Cidade
    payload += _tlv("58", "BR")
    payload += _tlv("59", _norm_text(merchant_name, 25))
    payload += _tlv("60", _norm_text(merchant_city, 15))

    # 62: Dados Adicionais (05: txid)
    add = _tlv("05", _sanitize_txid(txid))
    payload += _tlv("62", add)

    # 63: CRC16
    to_crc = payload + "6304"
    crc = _crc16_ccitt(to_crc)
    return to_crc + crc

def sanitize_phone(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    keep_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" + digits) if keep_plus else digits

def _int_or_zero(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default

def get_card_view_count(uid: str) -> int:
    db = db_defaults(load())
    card = db.get("cards", {}).get(uid) or {}
    metrics = card.get("metrics") or {}
    return _int_or_zero(metrics.get("views"), 0)

def increment_card_view(uid: str) -> int:
    db = db_defaults(load())
    card = db.get("cards", {}).get(uid)
    if not card:
        return 0
    metrics = card.setdefault("metrics", {})
    current = _int_or_zero(metrics.get("views"), 0) + 1
    metrics["views"] = current
    try:
        save(db)
    except Exception:
        pass
    return current

def should_track_view(request: Request, slug: str) -> bool:
    if request.method.upper() != "GET":
        return False
    referer = request.headers.get("referer", "")
    if not referer:
        return True
    try:
        parsed = urlparse.urlparse(referer)
    except ValueError:
        return True
    ref_host = (parsed.hostname or "").lower()
    current_host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if ref_host and current_host and ref_host != current_host:
        return True
    path = parsed.path or ""
    if path.startswith("/auth/logout"):
        return False
    normalized = {f"/{slug}", f"/u/{slug}"}
    if path in normalized:
        q_keys = {k.lower() for k in urlparse.parse_qs(parsed.query or "").keys()}
        if q_keys & {"pix", "offline"}:
            return False
    return True

def profile_complete(prof: dict) -> bool:
    if not prof:
        return False
    name = (prof.get("full_name") or "").strip()
    has_name = bool(name and name != "Seu Nome")
    has_contact = bool(
        (prof.get("whatsapp") or "").strip()
        or (prof.get("email_public") or "").strip()
        or (isinstance(prof.get("links"), list) and len(prof.get("links") or []) > 0)
    )
    return has_name and has_contact

def _request_host(request: Request | None) -> str:
    if not request:
        return ""
    host = (request.headers.get("host") or "").strip()
    if not host:
        return ""
    return host.split(":", 1)[0].lower()

def _card_public_base(card: dict | None, request: Request | None) -> str:
    active_host = active_custom_domain_host(card or {})
    if active_host:
        req_host = _request_host(request)
        scheme = (request.url.scheme if request else "https") or "https"
        if req_host and req_host == active_host:
            return f"{scheme}://{req_host}"
        return f"https://{active_host}"
    return PUBLIC_BASE

def _card_share_url(card: dict | None, slug: str, request: Request | None) -> str:
    base = _card_public_base(card or {}, request)
    slug_norm = (slug or "").strip().lstrip("/")
    if active_custom_domain_host(card or {}):
        return base
    return f"{base}/{slug_norm}"

def _card_entry_path(card: dict | None, slug: str) -> str:
    return "/" if active_custom_domain_host(card or {}) else f"/{slug}"

def resolve_photo(photo: str | None) -> str:
    if photo and str(photo).strip():
        return photo
    return DEFAULT_AVATAR
