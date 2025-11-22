# AGENTS.md — Guia de Arquitetura, Qualidade, Segurança e Deploy

Este documento orienta agentes e contribuidores em todo o repositório. Foca em boas práticas de engenharia (design, segurança, performance) e no caminho para produção.

## Visão Geral
- Produto: cartão digital com roteamento NFC + páginas públicas.
- Componentes:
  - API FastAPI (HTML server-rendered minimalista)
  - Worker Cloudflare (roteador curto `/r/{uid}` com KV/Queue)
  - Armazenamento local (MVP) em `api/data.json` — substituível por Postgres/D1.
  - Scripts para provisionar cartões (UID/PIN) e resetar.

## Arquitetura e Padrões de Projeto
- Estilo arquitetural: “Camadas finas” com evolução para Clean Architecture.
  - Camada Web (FastAPI endpoints, validação, DTOs)
  - Camada Domínio (regras: onboarding, slug, redirecionamento, sessão)
  - Camada Dados (repo/adapter: hoje JSON; futuramente Postgres/D1)
- Padrões recomendados:
  - DTOs/Pydantic para entrada/saída (futuramente)
  - Repository Pattern para isolamento de dados
  - Service/Use-Case para regras (ex.: ActivateCard, ChooseSlug)
  - Value Objects para Slug, Phone (normalização/validação)
  - Factory para tokens (e.g., EmailVerificationTokenFactory)
  - Estratégia de Hash (argon2/bcrypt) plugável
- Diretrizes:
  - Funções puras no domínio; side-effects nos adapters
  - Tipagem estática (mypy) quando evoluir
  - HTML mínimo renderizado no servidor; JS somente quando necessário

## Rotas e Fluxos (API)
- Primeira leitura NFC: `GET /{slug_or_uid}`
  - 404 se UID/slug inexistente
  - `pending` → `GET /onboard/{uid}`
  - Se possuir `vanity` e rota é por `uid` → redireciona para `/{vanity}`
  - Visitante vê cartão público; dono sem perfil completo → `/edit/{slug}`; dono sem vanity → `/slug/select/{uid}`
- Onboarding: `GET /onboard/{uid}` → `POST /auth/register`
  - Regras: LGPD aceito, PIN correto, senha ≥ 8, slug válido/único
  - E-mail: `GET /auth/verify?token=...` confirma
- Sessão: `POST /auth/login` → cookie `session`
- Logout: `POST /auth/logout`
- Público: `GET /u/{slug}`; vCard/QR: `GET /v/{slug}.vcf`, `GET /q/{slug}.png`
- Edição: `GET/POST /edit/{slug}` (nome, cargo, links, whatsapp, foto)
- Slug: `GET /slug/check?value=...` e `GET/POST /slug/select/{id}`

Rotas estáveis (não quebrar sem migração): `/onboard/*`, `/auth/*`, `/u/*`, `/q/*`, `/v/*`, `/{slug}`, `/slug/*`, `/blocked`.

## Admin (Subdomínio)
- App dedicado em `api/admin_app.py`; recomendado expor em `adm.seu-dominio` (ex.: `adm.soomei.cc`).
- Sessão separada: cookie `admin_session` (não reutilizar `session` do público).
- Requisitos de acesso (MVP):
  - E-mail verificado obrigatório (`email_verified_at` presente).
  - Allowlist por variável `ADMIN_EMAILS` (lista de e-mails, separada por vírgula). Fallback de dev: `@soomei.com.br`.
- CSRF/Origem (MVP):
  - Token CSRF por sessão incluído em todos os formulários (`csrf_token` oculto) e validado em cada POST state-changing.
  - Verificação de `Origin/Referer` contra `ADMIN_HOST` (aceita múltiplos, separados por vírgula). Em dev, `localhost:8001` e `127.0.0.1:8001` já são permitidos.
- Funcionalidades (MVP):
  - Dashboard: contagem de cartões por status.
  - Cartões: listar/filtrar; criar (`uid`, `pin`, `user?`, `vanity?`), bloquear, ativar, resetar (apaga dados relacionados e volta `pending` com novo PIN).
  - Usuários: listar; indica se é admin (pela allowlist) e status de verificação de e-mail.
- UI: Pico.css via CDN para rapidez; pode evoluir para Tailwind + DaisyUI.
- Execução Local (Admin):
  - `uvicorn api.admin_app:app --reload --port 8001`
- Produção (Admin):
  - Variáveis: `ADMIN_HOST=adm.seu-dominio`, `ADMIN_EMAILS=email1,email2`.
  - Cookies: ativar `Secure` atrás de HTTPS/reverse-proxy.
  - Preferencialmente atrás de WAF/Access (Cloudflare) e com rate limit de `/login`.

## Worker Cloudflare
- `GET /r/{uid}` lê KV `CARDS` e roteia para `API_BASE`:
  - `pending` → `/onboard/{uid}`; `blocked` → `/blocked`; `active` → `/{vanity|uid}`
- Variáveis: `API_BASE`. Métricas assíncronas via Queue `TAPS`.

## Segurança (Baseline → Produção)
- Criptografia de senha: `api.core.security` já usa `argon2id` (argon2-cffi) com prefixo próprio e fallback somente- leitura para hashes legados em scrypt; garantir que fluxos legados (ex.: troca de senha no `/edit/{slug}`) validem a senha atual via `verify_password`.
- Sessão: os cookies `session`/`admin_session` são emitidos via `session_service` e `api.admin_app` com `HttpOnly`, `SameSite=Strict` e `Secure` ativado quando `APP_ENV=prod` (ou `ADMIN_COOKIE_SECURE=1`). Tokens vivem no armazenamento server-side (`sessions`/`sessions_admin`) com TTL configurável (`SESSION_TTL_SECONDS` e `ADMIN_SESSION_TTL_SECONDS`).
- CSRF: `api.core.csrf` injeta cookie `csrf_token` (com `SameSite=Strict` e `Secure` em produção), exige POST + campo oculto e valida `Origin/Referer`. Rotas de login, register, slug select/update, edição de perfil, custom-domain, logout e admin já consomem esse helper.
- CORS: ainda precisamos travar `CORSMiddleware` para permitir somente o domínio público antes de liberar APIs JSON.
- Validações:
  - Slug (regex + lista reservada) — `api.domain.slugs`.
  - Telefone — `card_display.sanitize_phone`.
  - Uploads - hoje checamos content-type e reencodamos via Pillow, mas faltam limites de tamanho (ex.: 2MB), checagem de assinatura/MIME e quota de disco.
- Headers de segurança (prod):
  - `Content-Security-Policy` (default-src 'self')
  - `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer-when-downgrade`
  - `Strict-Transport-Security` (via proxy)
- Rate limiting: `api.core.rate_limiter.rate_limit_ip` limita slug-check/update, login/register/forgot e custom-domain; expandir para verify/admin APIs quando expostas.
- Auditoria/Observabilidade: Sentry/OTel para erros; logs estruturados com correlação (request-id) ainda pendentes.
- Segredos: variáveis de ambiente/secret manager. Nunca commitar chaves (KV/Queue IDs reais).

## Performance
- Server: Uvicorn com `--http h11 --loop uvloop` (Linux), workers por CPU atrás de proxy (NGINX/Cloudflare).
- Serialização: `orjson` para respostas JSON pesadas; compressão gzip/brotli no proxy.
- Cache:
  - CDN para assets estáticos (`web/`)
  - ETag/Last-Modified nas páginas estáticas simples
  - Worker: considerar caches curtíssimos para redirects `/{uid}` quando `vanity` estável (invalidação por evento)
- Banco: índices em `cards.slug`, `taps(slug, ts)`. Pool de conexões.
- Não bloquear loop: operações pesadas (ex.: QR, imagem) podem ir para tasks (ou pre-geradas/cached).

## Dados e Evolução para Produção
- Migrar `api/data.json` → Postgres (ou Cloudflare D1):
  - Tabelas: `users`, `cards`, `profiles`, `taps`, `email_tokens`, `sessions`
  - Migrations: Alembic
  - Hash de senha: argon2/bcrypt
- ETL de migração: script que lê JSON e popula DB.

## Observabilidade e Qualidade
- Logging estruturado (json) com nível por ambiente.
- Healthcheck `GET /healthz` e `GET /readyz` (adicionar quando for prod).
- Métricas: Prometheus/OpenTelemetry (latência, erros, throughput).
- Testes: pytest (unitários para domínio, integração para rotas críticas).
- Style: Black + Ruff + isort; pre-commit.

## Execução Local (Dev)
1) venv e deps:
```
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -r api\requirements.txt
```
2) API:
```
set PUBLIC_BASE_URL=http://localhost:8000
uvicorn api.app:app --reload --port 8000
```
2.1) Admin:
```
uvicorn api.admin_app:app --reload --port 8001
```
3) Worker (opcional):
```
cd cloudflare
wrangler dev --var API_BASE=http://localhost:8000
```
4) Scripts:
```
python scripts\add_card.py --uid abc123
python scripts\reset_card.py --uid abc123
```

## Deploy para Produção (Plano)
- Containerização (sugerido):
  - Dockerfile com Uvicorn/Gunicorn, `ENV PUBLIC_BASE_URL=https://seu.dominio`
  - Healthchecks; read-only FS; usuário não-root.
- Infra:
  - Banco gerenciado (Postgres/D1), WAF/Proxy (Cloudflare), TLS, domínio.
  - Secrets via gerenciador (Cloudflare/Wrangler secrets, provedor da cloud, ou GitHub Actions Secrets).
- Pipeline (CI/CD):
  - Lint + testes (pytest) em PR
  - Build/push de imagem; deploy automatizado (GitHub Actions) e `wrangler deploy` para o Worker.
- Pós-deploy:
  - Rodar migrations
  - Criar usuário admin inicial (script)
  - Verificar métricas/erros

Notas de Segurança do Admin
- [x] Sessão e cookie dedicados (`admin_session`).
- [x] E-mail verificado requerido no login do admin.
- [x] CSRF obrigatório em POSTs do admin + validação de origem via `ADMIN_HOST`.
- [x] Cookie `Secure` e `SameSite=Strict` (o `admin_session` sempre usa HttpOnly+SameSite=Strict e liga `Secure` quando `ADMIN_COOKIE_SECURE=1` ou `APP_ENV=prod`).
- [ ] 2FA (TOTP) para contas admin (futuro).

Checklist de Segurança em Produção
- [x] HTTPS forçado (HSTS via proxy) — o middleware de segurança injeta `Strict-Transport-Security` automaticamente quando `APP_ENV=prod`; basta manter o app atrás de HTTPS real/proxy.
- [x] Cookies `Secure; HttpOnly; SameSite=Lax`/`Strict` (cookies de sessão/admin já aplicam `HttpOnly` + `SameSite=Strict` e habilitam `Secure` quando `APP_ENV=prod`; o cookie de CSRF segue exposto por design).
- [x] CSRF em endpoints state-changing (FastAPI usa `api.core.csrf` com cookie + header e validação de origem).
- [x] Rate limiting de login/register/verify (rate limit por IP em login, register, forgot e slug/custom-domain; considerar adicionar verify/admin).
- [x] CSP e cabeçalhos de segurança ativos (middleware injeta CSP opinativa + XFO, X-Content-Type-Options, Referrer-Policy; HSTS quando `APP_ENV=prod`).
- [x] Limites de upload e validação de arquivo (fluxo `/edit/{slug}` agora limita a 2MB e valida assinatura JPEG/PNG antes de salvar).
- [x] Hash de senha com argon2/bcrypt (`api.core.security` usa Argon2id).
- [ ] Logs estruturados e rastreabilidade
- [ ] Backup/retention do DB e KV

## Scripts Úteis
- `scripts/add_card.py` — cadastra cartão pendente (UID/PIN/vanity)
- `scripts/reset_card.py` — reseta cartão, remove dados, recria como `pending`
- `scripts/write_tags.py` — grava URLs NFC (ajustar domínio conforme ambiente)

## Convenções de Código
- PT-BR nos rótulos/mensagens. UTF-8.
- Ao embutir JS em f-strings, sempre escapar `{`/`}` como `{{`/`}}` (especialmente em template literals).
- Sanitizar/escapar toda entrada apresentada (usar `html.escape`).
- Não versionar `web/uploads/`, `__pycache__`, venv.

## Contribuição
- Commits: `tipo(escopo): resumo` (ex.: `feat(api): valida slug no cadastro`).
- Abrir PRs com descrição clara e impacto em rotas/segurança.
- Respeitar rotas estáveis; se quebrar, documentar migração.

### Modularizacao em andamento
- Dados/adapters: `api.repositories.json_storage` centraliza `load/save/db_defaults` para o JSON enquanto não migramos para Postgres/D1.
- Domínio: `api.domain.slugs` concentra regras de slug/nomes reservados e é reutilizado pelos serviços.
- Serviços:
  - `api.services.slug_service` (checagem e atribuição de slug), `session_service` (emissão/lookup de sessões) e `auth_service` (cadastro/login/verify/password reset).
  - `api.services.card_service` (lookup de cartões) e `card_display` (renderização, métricas, normalizações de links/Pix).
  - `api.services.custom_domain_service` expõe fluxo de request/withdraw/remove para domínios personalizados (`CUSTOM_DOMAINS_ENABLED=1` habilita o router) sobre a base compartilhada de `api.services.domain_service` (normalize/lookup/registry reutilizado por outros módulos).
- Core cross-cutting: `api.core.config`, `security` (argon2), `csrf`, `rate_limiter`, `mailer` e `utils` encapsulam concerns compartilhados.
- Routers FastAPI:
  - `api.routers.auth`, `slug`, `custom_domain`, `pages` (onboard/login/terms) e `hooks` já estão isolados; `api.routers.card_edit` cuida da edição fora do monólito.
  - `api.routers.cards` continua grande e concentra renderização pública, QR, vCard e onboarding; próxima etapa é quebrá-lo em módulos menores (público vs métricas) sem depender de templates inline.

