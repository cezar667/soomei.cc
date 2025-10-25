# Soomei — Cartão NFC + Cartão Digital (MVP)

MVP do cartão de visita digital com QR e NFC:
- Router curto no Cloudflare (`/r/{uid}`) que redireciona para a página pública do cartão
- API em FastAPI para páginas públicas, QR e vCard (HTML server‑rendered)
- Scripts para gravação NFC (NTAG213/215)

## Estrutura
- `cloudflare/` — Worker do Cloudflare
  - Rota: `/r/{uid}` (lê `CARDS` no KV e redireciona para `/{slug|uid}` ou `/u/{slug}`)
  - Métricas assíncronas via Queue `TAPS`
  - Configurar `cloudflare/wrangler.toml` com seu KV e Queue
- `api/` — FastAPI + estáticos simples
  - Endpoints principais:
    - `GET /onboard/{uid}` — ativação inicial com PIN
    - `POST /auth/register` e `POST /auth/login`
    - `GET /u/{slug}` — página pública do cartão
    - `GET /{slug}` — página pública alternativa (vanity)
    - `GET /q/{slug}.png` — QR code
    - `GET /v/{slug}.vcf` — vCard 3.0
    - `POST /hooks/themembers` — webhook de billing/status
  - Banco (MVP): JSON local em `api/data.json`
- `web/` — CSS básico para as páginas
- `db/schema.sql` — esboço de schema (Postgres/D1) para evolução
- `scripts/` — utilitários (ex.: `write_tags.py` para gravar NFC)

## Executar a API (FastAPI)
Pré‑requisitos: Python 3.11+.

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
- `http://localhost:8000/abc123` (rota do cartão/NFC)
- `http://localhost:8000/onboard/abc123`
- `http://localhost:8000/q/abc123.png`
- `http://localhost:8000/v/abc123.vcf`

Observação: `PUBLIC_BASE_URL` controla o domínio usado para QR/vCard. Em produção, defina como `https://soomei.cc`.

## Cloudflare Worker (Router curto)
Pré‑requisitos: `wrangler` instalado e logado (`npm i -g wrangler`).

1. Edite `cloudflare/wrangler.toml` com KV/Queue e a variável `API_BASE` (por padrão `https://soomei.cc`).
2. Desenvolver localmente (redirecionando para a API local):
```
cd cloudflare
wrangler dev --var API_BASE=http://localhost:8000
```
3. Deploy:
```
wrangler deploy
```

## Gravação NFC (NTAG213/215)
Pré‑requisitos: leitor NFC compatível e `nfcpy`.

```
pip install nfcpy
python scripts\write_tags.py  # usa slugs de scripts\slugs.csv
```

O script grava a URL do slug no chip. Ajuste o domínio conforme necessário.

## Roadmap (próximos passos)
- Migrar JSON local para Postgres ou D1
- Autenticação e painel do usuário
- Logs e relatórios de taps
- Admin em subdomínio dedicado (`adm.soomei.cc`) com sessão/2FA separados

## Desenvolvimento
Contribuições são bem‑vindas. Abra issues e PRs no GitHub.

## Para Contribuidores
- Leia as diretrizes em `AGENTS.md` (arquitetura, padrões, segurança, performance e deploy).
- Siga os padrões de commit (`tipo(escopo): resumo`) e evite quebrar as rotas estáveis.
- Em mudanças que afetem produção, atualize o `AGENTS.md` e este README.

## Versionamento e Releases

- Adotamos SemVer: `vMAJOR.MINOR.PATCH`.
- Fluxo sugerido:
  - Features/ajustes: incremente `MINOR` (ex.: `v0.2.0`).
  - Hotfixes: incremente `PATCH` (ex.: `v0.1.1`).
- Como cortar uma release:
  1. Garanta que `main` está atualizado e verde.
  2. Crie uma tag anotada: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
  3. Publique: `git push origin main --follow-tags`
  4. O GitHub Actions cria o Release automaticamente (workflow `release.yml`).

- Histórico de mudanças: consulte `CHANGELOG.md`.

