# Soomei â€” CartÃ£o NFC + CartÃ£o Digital (MVP)

MVP do cartÃ£o de visita digital com QR e NFC:
- Router curto no Cloudflare (`/r/{uid}`) â†’ redireciona para pÃ¡gina pÃºblica do cartÃ£o
- API em FastAPI para pÃ¡ginas pÃºblicas, QR e vCard
- Script para gravaÃ§Ã£o NFC (NTAG213/215)

## Estrutura
- `cloudflare/` â€” Worker do Cloudflare
  - Rota: `/r/{uid}` (lÃª `CARDS` no KV e redireciona para `/u/{slug|uid}`)
  - MÃ©tricas assÃ­ncronas via Queue `TAPS`
  - Configurar `cloudflare/wrangler.toml` com seu KV e Queue
- `api/` â€” FastAPI + estÃ¡ticos simples
  - Endpoints principais:
    - `GET /onboard/{uid}` â€” ativaÃ§Ã£o inicial com PIN
    - `POST /auth/register` e `POST /auth/login`
    - `GET /u/{slug}` â€” pÃ¡gina pÃºblica do cartÃ£o
    - `GET /q/{slug}.png` â€” QR code
    - `GET /v/{slug}.vcf` â€” vCard 3.0
    - `POST /hooks/themembers` â€” webhook de billing/status
  - â€œBancoâ€ JSON local em `api/data.json` (MVP)
- `web/` â€” CSS bÃ¡sico para as pÃ¡ginas
- `db/schema.sql` â€” esboÃ§o de schema (Postgres/D1) para evoluÃ§Ã£o
- `scripts/` â€” utilitÃ¡rios (ex.: `write_tags.py` para gravar NFC)

## Executar a API (FastAPI)
PrÃ©â€‘requisitos: Python 3.11+.

Windows (PowerShell):
```
python -m venv .venv
.\.venv\Scripts\activate
pip install -r api\requirements.txt
set PUBLIC_BASE_URL=http://localhost:8000
uvicorn api.app:app --reload --port 8000
```

macOS/Linux:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt
export PUBLIC_BASE_URL=http://localhost:8000
uvicorn api.app:app --reload --port 8000
```

Depois, acesse:
- `http://localhost:8000/abc123` (rota do cartÃ£o/NFC)
- `http://localhost:8000/onboard/abc123`
- `http://localhost:8000/q/abc123.png`
- `http://localhost:8000/v/abc123.vcf`

ObservaÃ§Ã£o: `PUBLIC_BASE_URL` controla o domÃ­nio usado para QR/vCard. Em produÃ§Ã£o, defina como `https://soomei.cc`.

## Cloudflare Worker (Router curto)
PrÃ©â€‘requisitos: `wrangler` instalado e logado (`npm i -g wrangler`).

1. Edite `cloudflare/wrangler.toml` com KV/Queue e a variÃ¡vel `API_BASE` (por padrÃ£o `https://soomei.cc`).
2. Desenvolver localmente (redirecionando para a API local):
```
cd cloudflare
wrangler dev --var API_BASE=http://localhost:8000
```
3. Deploy:
```
wrangler deploy
```

## GravaÃ§Ã£o NFC (NTAG213/215)
PrÃ©â€‘requisitos: leitor NFC compatÃ­vel e `nfcpy`.

```
pip install nfcpy
python scripts\write_tags.py  # usa slugs de scripts\slugs.csv
```

O script grava a URL do slug no chip. Ajuste o domÃ­nio conforme necessÃ¡rio.

## Roadmap (prÃ³ximos passos)
- Migrar JSON local para Postgres ou D1
- AutenticaÃ§Ã£o e painel do usuÃ¡rio
- Logs e relatÃ³rios de taps

## Desenvolvimento
Contribuições são bem‑vindas. Abra issues e PRs no GitHub.


## Para Contribuidores
- Leia as diretrizes em AGENTS.md (arquitetura, padrões, segurança, performance e deploy).
- Siga os padrões de commit (	ipo(escopo): resumo) e evite quebrar as rotas estáveis.
- Em mudanças que afetem produção, atualize o AGENTS.md e este README.

