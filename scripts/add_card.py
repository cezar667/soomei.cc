#!/usr/bin/env python3
"""
Cadastrar um novo cartao (UID) diretamente no Postgres.

Uso:
  python scripts/add_card.py --uid abc123 [--pin 654321] [--vanity meu-slug] [--owner email@dominio]
"""
from __future__ import annotations

import argparse
import secrets
import sys

from api.domain.slugs import is_valid_slug
from api.repositories.sql_repository import SQLRepository


def gen_pin(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def main() -> None:
    ap = argparse.ArgumentParser(description="Cadastrar cartao no Postgres")
    ap.add_argument("--uid", required=True, help="UID do cartao (ex.: abc123)")
    ap.add_argument("--pin", help="PIN numerico (default: aleatorio de 6 digitos)")
    ap.add_argument("--vanity", help="Slug opcional (ex.: meu-nome)")
    ap.add_argument("--owner", help="Email do dono (deve existir em users)")
    args = ap.parse_args()

    repo = SQLRepository()
    uid = (args.uid or "").strip()
    if not uid:
        raise SystemExit("UID invalido")
    if repo.get_card_by_uid(uid):
        raise SystemExit(f"UID '{uid}' ja existe no banco")
    vanity = (args.vanity or "").strip()
    if vanity:
        if not is_valid_slug(vanity):
            raise SystemExit("Slug invalido (use 3-30 chars [a-z0-9-])")
        if repo.slug_exists(vanity):
            raise SystemExit(f"Slug '{vanity}' ja esta em uso")
    owner = (args.owner or "").strip() or None
    if owner and not repo.get_user(owner):
        raise SystemExit(f"Usuario '{owner}' nao existe")

    pin = (args.pin or "").strip() or gen_pin()
    if not pin.isdigit():
        raise SystemExit("PIN deve ser numerico")

    repo.create_card(uid, pin, vanity or None, owner)
    if owner:
        repo.assign_card_owner(uid, owner, status="pending")
    print("OK: cartao cadastrado")
    print(f"  UID: {uid}")
    print(f"  PIN: {pin}")
    if vanity:
        print(f"  Vanity: {vanity}")
    if owner:
        print(f"  Dono: {owner}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - uso CLI
        sys.stderr.write(f"Erro: {exc}\n")
        raise SystemExit(1)
