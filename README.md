# Soomei — Cartão NFC + Cartão Digital (MVP)

Este repositório inicia a implementação do **Router curto** + **Páginas públicas** do cartão de visita digital (com QR) e scripts para **gravação NFC**.

## Stack (MVP)
- **Cloudflare Workers** (Router `/r/{slug}`) usando **KV** e **Queue** para métricas.
- **FastAPI** para páginas públicas (`/u/{slug}`), **QR** (`/q/{slug}.png`) e **vCard** (`/v/{slug}.vcf`).
- Script **nfcpy** para gravação **NTAG213/215** com `https://smei.cc/{slug}`.

## Como rodar
1) Cloudflare Worker em `cloudflare/` (wrangler).
2) API FastAPI em `api/` (uvicorn).
3) Script NFC em `scripts/`.
