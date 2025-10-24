#!/usr/bin/env python3
"""
scripts/add_card.py — cadastra um novo cartão (UID) para onboarding.

Uso:
  python scripts/add_card.py --uid abc123 [--pin 654321] [--vanity meu-slug]

Comportamento:
  - Atualiza api/data.json adicionando o UID com status "pending" e PIN informado
    (ou gera PIN numérico aleatório de 6 dígitos).
  - Não sobrescreve cartões existentes com o mesmo UID.
"""

import argparse
import json
import os
import secrets

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "..", "api", "data.json")


def load_db():
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            try:
                db = json.load(f)
            except Exception:
                db = {}
    else:
        db = {}
    # garante chaves padrão
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


def main():
    ap = argparse.ArgumentParser(description="Cadastrar novo cartão para onboarding")
    ap.add_argument("--uid", required=True, help="UID do cartão (ex.: abc123)")
    ap.add_argument("--pin", help="PIN numérico (default: aleatório de 6 dígitos)")
    ap.add_argument("--vanity", help="Slug personalizado opcional (ex.: meu-nome)")
    args = ap.parse_args()

    db = load_db()

    uid = args.uid.strip()
    if not uid:
        raise SystemExit("UID inválido")

    if uid in db["cards"]:
        raise SystemExit(f"UID '{uid}' já existe em cards")

    pin = args.pin.strip() if args.pin else gen_pin()
    if not pin.isdigit():
        raise SystemExit("PIN deve ser numérico")

    card = {
        "uid": uid,
        "status": "pending",
        "pin": pin,
    }
    if args.vanity:
        # checa se vanity já está em uso por outro cartão
        for _, c in db["cards"].items():
            if c.get("vanity") == args.vanity:
                raise SystemExit(f"Vanity '{args.vanity}' já está em uso")
        card["vanity"] = args.vanity

    db["cards"][uid] = card
    save_db(db)

    print("OK: cartão cadastrado")
    print(f"  UID: {uid}")
    print(f"  PIN: {pin}")
    if "vanity" in card:
        print(f"  Vanity: {card['vanity']}")
    print(f"Arquivo salvo: {os.path.abspath(DATA)}")


if __name__ == "__main__":
    main()

