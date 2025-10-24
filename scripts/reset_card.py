#!/usr/bin/env python3
"""
scripts/reset_card.py — Reinicia um cartão pelo UID, removendo dados
relacionados e recriando o cartão em estado "pending" com novo PIN.

Uso:
  python scripts/reset_card.py --uid abc123 [--new-pin 654321]

O que faz:
  - Remove o cartão do UID informado, apaga foto relacionada (web/uploads/{uid}.jpg|.png).
  - Desvincula o usuário; se ele não possuir outros cartões, remove o perfil,
    sessões e tokens de verificação, e também o usuário.
  - Recria o cartão {uid} com status "pending" e PIN informado (ou aleatório).
"""

import argparse
import json
import os
import secrets
from typing import Tuple

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "..", "api", "data.json")
WEB = os.path.join(BASE, "..", "web")
UPLOADS_DIR = os.path.join(WEB, "uploads")


def load_db():
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            try:
                db = json.load(f)
            except Exception:
                db = {}
    else:
        db = {}
    db.setdefault("users", {})
    db.setdefault("cards", {})
    db.setdefault("profiles", {})
    db.setdefault("sessions", {})
    db.setdefault("verify_tokens", {})
    return db


def save_db(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def gen_pin(length=6):
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def count_cards_of_user(db, email: str) -> int:
    c = 0
    for _, card in db.get("cards", {}).items():
        if card.get("user") == email:
            c += 1
    return c


def delete_photo(uid: str) -> Tuple[bool, str]:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    removed = False
    last = ""
    for ext in (".jpg", ".png"):
        path = os.path.join(UPLOADS_DIR, f"{uid}{ext}")
        if os.path.exists(path):
            try:
                os.remove(path)
                removed = True
                last = path
            except Exception:
                pass
    return removed, last


def main():
    ap = argparse.ArgumentParser(description="Reiniciar cartão pelo UID")
    ap.add_argument("--uid", required=True, help="UID do cartão a reiniciar")
    ap.add_argument("--new-pin", help="Novo PIN do cartão (numérico). Default: aleatório de 6 dígitos")
    args = ap.parse_args()

    uid = args.uid.strip()
    if not uid:
        raise SystemExit("UID inválido")

    db = load_db()
    card = db["cards"].get(uid)
    if not card:
        print(f"Aviso: UID '{uid}' não existia. Será criado do zero.")
        owner = None
    else:
        owner = card.get("user")

    # Remove cartão
    if uid in db["cards"]:
        del db["cards"][uid]

    # Remove foto do cartão
    photo_removed, photo_path = delete_photo(uid)

    # Se havia dono, checa se é órfão (sem outros cartões)
    removed_user = False
    removed_profile = False
    removed_sessions = 0
    removed_tokens = 0
    if owner:
        others = count_cards_of_user(db, owner)
        if others == 0:
            # remove perfil
            if owner in db["profiles"]:
                del db["profiles"][owner]
                removed_profile = True
            # remove usuário
            if owner in db["users"]:
                del db["users"][owner]
                removed_user = True
            # remove sessões do usuário
            for token, meta in list(db.get("sessions", {}).items()):
                if meta.get("email") == owner:
                    del db["sessions"][token]
                    removed_sessions += 1
            # remove tokens de verificação
            for token, meta in list(db.get("verify_tokens", {}).items()):
                if meta.get("email") == owner:
                    del db["verify_tokens"][token]
                    removed_tokens += 1

    # Recria cartão pendente com novo PIN
    new_pin = (args.new_pin.strip() if args.new_pin else gen_pin())
    if not new_pin.isdigit():
        raise SystemExit("--new-pin deve ser numérico")
    db["cards"][uid] = {"uid": uid, "status": "pending", "pin": new_pin}

    save_db(db)

    print("OK: cartão reiniciado")
    print(f"  UID: {uid}")
    print(f"  Novo PIN: {new_pin}")
    if owner:
        print(f"  Antigo dono: {owner}")
    if photo_removed:
        print(f"  Foto removida: {photo_path}")
    print("  Remoções relacionadas:")
    print(f"    Usuário removido: {'sim' if removed_user else 'não'}")
    print(f"    Perfil removido: {'sim' if removed_profile else 'não'}")
    print(f"    Sessões removidas: {removed_sessions}")
    print(f"    Tokens de verificação removidos: {removed_tokens}")
    print(f"Arquivo salvo: {os.path.abspath(DATA)}")


if __name__ == "__main__":
    main()

