#!/usr/bin/env python3
"""
Resetar um cartao (UID) no Postgres: limpa dono/vanity/domínio, zera views e define novo PIN.

Uso:
  python scripts/reset_card.py --uid abc123 [--new-pin 654321]
"""
from __future__ import annotations

import argparse
import secrets
import sys

from api.repositories.sql_repository import SQLRepository


def gen_pin(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def cleanup_user_if_orphan(repo: SQLRepository, email: str, uid: str) -> None:
    others = [c for c in repo.get_cards_by_owner(email) if c.uid != uid]
    if others:
        return
    repo.delete_profile(email)
    repo.delete_user_sessions(email)
    repo.delete_verify_tokens_for_email(email)
    repo.delete_reset_tokens_for_email(email)
    repo.delete_user(email)


def main() -> None:
    ap = argparse.ArgumentParser(description="Resetar cartao no Postgres")
    ap.add_argument("--uid", required=True, help="UID do cartao a resetar")
    ap.add_argument("--new-pin", help="Novo PIN numerico (default: aleatorio de 6 digitos)")
    args = ap.parse_args()

    repo = SQLRepository()
    uid = (args.uid or "").strip()
    if not uid:
        raise SystemExit("UID invalido")
    card = repo.get_card_by_uid(uid)
    if not card:
        raise SystemExit(f"UID '{uid}' nao encontrado")
    owner = card.owner_email

    pin = (args.new_pin or "").strip() or gen_pin()
    if not pin.isdigit():
        raise SystemExit("PIN deve ser numerico")

    repo.reset_card(uid, new_pin=pin, clear_owner=True, clear_vanity=True, clear_custom_domain=True)
    if owner:
        cleanup_user_if_orphan(repo, owner, uid)

    print("OK: cartao resetado")
    print(f"  UID: {uid}")
    print(f"  Novo PIN: {pin}")
    if owner:
        print(f"  Antigo dono removido: {owner}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"Erro: {exc}\n")
        raise SystemExit(1)
