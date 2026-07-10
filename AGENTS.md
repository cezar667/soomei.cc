# AGENTS.md — Guia de Arquitetura, Qualidade, Segurança e Deploy

Este documento orienta agentes e contribuidores em todo o repositório. Foca em boas práticas de engenharia (design, segurança, performance) e no caminho para produção.

## Visão Geral
- Produto: cartão digital com roteamento NFC + páginas públicas.
- Componentes:
  - API FastAPI (HTML server-rendered minimalista)
  - Worker Cloudflare (roteador curto `/r/{uid}` com KV/Queue)
  - Banco principal: Postgres (SQLAlchemy). `api/data.json` fica apenas para importação legada via `scripts/migrate_to_postgres.py`.
  - Scripts para provisionar cartões (UID/PIN) e resetar.

## Arquitetura e Padrões de Projeto
- Estilo arquitetural: “Camadas finas” com evolução para Clean Architecture.
  - Camada Web (FastAPI endpoints, validação, DTOs)
  - Camada Domínio (regras: onboarding, slug, redirecionamento, sessão)
  - Camada Dados (repo/adapters em SQLAlchemy/Postgres; pode evoluir para D1)
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
- App dedicado em `api/admin_app.py`; recomendado expor em `adm.seu-dominio` (ex.: `adm.soomei.cc`).
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
  - `uvicorn api.admin_app:create_admin_app --reload --port 8001`
- Produção (Admin):
  - Variáveis: `ADMIN_HOST=adm.seu-dominio`, `ADMIN_EMAILS=email1,email2`.
  - Cookies: ativar `Secure` atrás de HTTPS/reverse-proxy.
  - Preferencialmente atrás de WAF/Access (Cloudflare) e com rate limit de `/login`.

## Worker Cloudflare
- `GET /r/{uid}` lê KV `CARDS` e roteia para `API_BASE`:
  - `pending` → `/onboard/{uid}`; `blocked` → `/blocked`; `active` → `/{vanity|uid}`
- Variáveis: `API_BASE`. Métricas assíncronas via Queue `TAPS`.
- Situação atual: o Worker continua dependente de KV como fonte de verdade no edge, mas o backend não publica automaticamente mutações de cartão para `CARDS`; antes de produção, ativação/bloqueio/reset/troca de slug precisam sincronizar Postgres → KV.

## Segurança (Baseline → Produção)
- Criptografia de senha: `api.core.security` já usa `argon2id` (argon2-cffi) com prefixo próprio e fallback somente- leitura para hashes legados em scrypt; garantir que fluxos legados (ex.: troca de senha no `/edit/{slug}`) validem a senha atual via `verify_password`.
- Sessão: os cookies `session`/`admin_session` são emitidos via `session_service` e `api.admin_app` com `HttpOnly`, `SameSite=Strict` e `Secure` ativado quando `APP_ENV=prod` (ou `ADMIN_COOKIE_SECURE=1`). Tokens vivem no armazenamento server-side (`sessions`/`sessions_admin`) com TTL configurável (`SESSION_TTL_SECONDS` e `ADMIN_SESSION_TTL_SECONDS`).
- CSRF: `api.core.csrf` injeta cookie `csrf_token` (com `SameSite=Strict` e `Secure` em produção), exige POST + campo oculto e valida `Origin/Referer`. Rotas de login, register, slug select/update, edição de perfil, custom-domain, logout e admin já consomem esse helper.
- CORS: `CORSMiddleware` já restringe origens ao `PUBLIC_BASE` (mais localhost/127.0.0.1 em dev); revisar se novos hosts forem expostos.
- Validações:
  - Slug (regex + lista reservada) — `api.domain.slugs`.
  - Telefone — `card_display.sanitize_phone`.
  - Uploads - fluxo `/edit/{slug}` limita a 2MB, reencoda e valida assinatura JPEG/PNG; ainda faltam quotas/limite de disco e limpeza de arquivos órfãos.
- Headers de segurança (prod):
  - `Content-Security-Policy` (default-src 'self')
  - `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer-when-downgrade`
  - `Strict-Transport-Security` (via proxy)
- Rate limiting: `api.core.rate_limiter.rate_limit_ip` já cobre slug-check/update, login/register/forgot, `/auth/verify`, reenvios, custom-domain e `/login` do admin. O gap remanescente é operacional: o limiter continua in-memory por processo e não escala para múltiplos workers/instâncias.
- Admin: `api.admin_app` já reutiliza o middleware compartilhado de headers de segurança e expõe `POST /logout` com CSRF. Antes de produção mais séria, faltam 2FA/TOTP, trilha de auditoria e rate limit distribuído.
- Auditoria/Observabilidade: Sentry/OTel para erros; logs estruturados com correlação (request-id) ainda pendentes.
- Segredos: variáveis de ambiente/secret manager. Nunca commitar chaves (KV/Queue IDs reais).

## Performance
- Server: Uvicorn com `--http h11 --loop uvloop` (Linux), workers por CPU atrás de proxy (NGINX/Cloudflare).
- Serialização: `orjson` para respostas JSON pesadas; compressão gzip/brotli no proxy.
- Cache:
  - CDN para assets estáticos (`web/`)
  - ETag/Last-Modified nas páginas estáticas simples
  - Worker: considerar caches curtíssimos para redirects `/{uid}` quando `vanity` estável (invalidação por evento)
- Banco: além de PK/unique do ORM, já existem índices explícitos para consultas frequentes (`cards.owner_email`, `cards.status`, `sessions.expires_at`, `verify_tokens.email`, `reset_tokens.email`, `custom_domains.card_uid`) no ORM e no baseline do Alembic. Dashboard/admin/domínios críticos já usam paginação/filtros SQL em vez de full scan em memória.
- Métricas: `metrics_views` é incrementado de forma síncrona a cada visita; para volume real, mover coleta para fila/eventos e agregar de forma assíncrona.
- Não bloquear loop: operações pesadas (QR, vCard com foto, imagem) ainda acontecem durante requests; o fluxo de upload já usa `asyncio.to_thread` em `card_edit`, mas a geração pública continua síncrona e deve ser cacheada/precomputada quando houver tráfego maior.
- Estado atual crítico: `sync_card_from_json` foi removido dos caminhos de leitura públicos e de edição; o helper fica restrito a import/backfill legado.

## Dados e Evolução para Produção
- Fonte de dados: Postgres (`users`, `cards`, `profiles`, `sessions`, `sessions_admin`, `verify_tokens`, `reset_tokens`, `custom_domains`) via `api.db.models`/`create_tables`, com baseline versionado em `alembic/` (`20260505_0001`). Próximas mudanças de schema devem seguir migration incremental, não `create_all()` ad-hoc.
- `db/schema.sql` está defasado e não representa o runtime atual; não tratar esse arquivo como fonte de verdade até ser reescrito ou substituído por migrations versionadas.
- Import legado: `api/data.json` pode ser carregado via `python scripts/migrate_to_postgres.py`; não é usado em runtime.
- Edge/read model: falta uma projeção oficial Postgres → Cloudflare KV (`CARDS`) para manter o Worker consistente com slug/status/dono.

## Observabilidade e Qualidade
- Logging estruturado (json) com nível por ambiente.
- Healthcheck `GET /healthz` e `GET /readyz` (adicionar quando for prod).
- Métricas: Prometheus/OpenTelemetry (latência, erros, throughput).
- Testes: hoje existem apenas smoke/unit tests para `SQLRepository` e `AuthService`; faltam integração/E2E para onboarding, páginas públicas, uploads, admin, custom-domain e Worker.
- Pipeline: o repositório só possui workflow de release/deploy por tag; ainda faltam validação em PR (lint/testes), deploy do Worker, execução de migrations e smoke test pós-deploy.
- Ambiente dev: padronizar Python 3.11+ com dependências de runtime/dev reproduzíveis (`requirements-dev.txt` ou `pyproject` + lock), evitando coleta de testes quebrada por ambiente incompleto.
- Style: Black + Ruff + isort; pre-commit.

## Execução Local (Dev)
1) venv e deps:
```
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -r api\requirements.txt
```
2) Banco/migrations:
```
set DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/soomei
alembic upgrade head
```
3) API:
```
set PUBLIC_BASE_URL=http://localhost:8000
uvicorn api.app:create_app --reload --port 8000
```
3.1) Admin:
```
uvicorn api.admin_app:create_admin_app --reload --port 8001
```
4) Worker (opcional):
```
cd cloudflare
wrangler dev --var API_BASE=http://localhost:8000
```
5) Scripts:
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
- [x] Rate limit em `/login` do admin.
- [ ] 2FA (TOTP) para contas admin (futuro).

Checklist de Segurança em Produção
- [x] HTTPS forçado (HSTS via proxy) — o middleware de segurança injeta `Strict-Transport-Security` automaticamente quando `APP_ENV=prod`; basta manter o app atrás de HTTPS real/proxy.
- [x] Cookies `Secure; HttpOnly; SameSite=Lax`/`Strict` (cookies de sessão/admin já aplicam `HttpOnly` + `SameSite=Strict` e habilitam `Secure` quando `APP_ENV=prod`; o cookie de CSRF segue exposto por design).
- [x] CSRF em endpoints state-changing (FastAPI usa `api.core.csrf` com cookie + header e validação de origem).
- [x] Rate limiting funcional nos endpoints críticos do público e no `/login` do admin.
- [ ] Rate limiting distribuído/centralizado para múltiplas instâncias.
- [x] CSP e cabeçalhos de segurança ativos (middleware injeta CSP opinativa + XFO, X-Content-Type-Options, Referrer-Policy; HSTS quando `APP_ENV=prod`).
- [x] Limites de upload e validação de arquivo (fluxo `/edit/{slug}` agora limita a 2MB e valida assinatura JPEG/PNG antes de salvar).
- [x] Hash de senha com argon2/bcrypt (`api.core.security` usa Argon2id).
- [ ] Logs estruturados e rastreabilidade
- [ ] Backup/retention do DB e KV

## Scripts Úteis
- `scripts/add_card.py` — cadastra cartão pendente (UID/PIN/vanity)
- `scripts/reset_card.py` — reseta cartão, remove dados, recria como `pending`
- `scripts/write_tags.py` — grava URLs NFC (ajustar domínio conforme ambiente)

## Notas recentes (dev)
- Verificação de e-mail: tokens guardados em timezone offset agora são normalizados para UTC antes do TTL (evita 400 no `/auth/verify`).
- Troca de e-mail pendente atualiza o botão de confirmação (dev) com o novo `verify_path` e retorna erro específico de PIN.
- Página `/auth/pending` usa o mesmo conteúdo de confirmação que o cadastro e reenvia o e-mail ao entrar.
- Público: visitante só vê “cartão em construção” se o perfil estiver incompleto, pendente ou dono não verificado; caso contrário, exibe o cartão.
- Testes: adicionado coverage para `_token_expired` e `change_pending_email` (SQLite temporário).

## Convenções de Código
- PT-BR nos rótulos/mensagens. UTF-8.
- Ao embutir JS em f-strings, sempre escapar `{`/`}` como `{{`/`}}` (especialmente em template literals).
- Sanitizar/escapar toda entrada apresentada (usar `html.escape`).
- Não versionar `web/uploads/`, `__pycache__`, venv.

## Contribuição
- Commits: `tipo(escopo): resumo` (ex.: `feat(api): valida slug no cadastro`).
- Abrir PRs com descrição clara e impacto em rotas/segurança.
- Respeitar rotas estáveis; se quebrar, documentar migração.

### Modularizacao e estado atual
- Dados/adapters:
  - Postgres em `api.db` (`session.py`, `models.py`, `create_tables.py`). `SQLRepository` cobre CRUD de usuarios/cards/perfis/tokens/sessoes/dominios, mais buscas paginadas/agrupamentos para admin (`search_cards`, `search_users`, `dashboard_card_counts`, `top_cards_by_views`, `list_cards_with_custom_domains`).
  - `sync_card_from_json` ficou restrito a import/backfill legado; as rotas públicas e de edição não persistem mais durante leitura/render.
- Dominio/Servicos:
  - `api.domain.slugs` segue com regras/reservados; `api.services.slug_service` usa apenas o repositorio SQL.
  - `api.services.card_service` resolve cards direto no banco; helpers de sync so para legado.
  - `api.services.session_service` usa tabela `sessions`; `api.services.auth_service` usa Postgres para registro/login/verificacao/reset.
  - `api.services.custom_domain_service`/`domain_service` consultam somente SQL (`custom_domains` + meta JSON no card), inclusive checagem de conflito sem full scan em memória.
- Core cross-cutting: `api.core.config`, `security` (argon2), `csrf`, `rate_limiter`, `http_security`, `mailer` e `utils`.
- Routers FastAPI:
  - `auth`, `slug`, `custom_domain`, `pages`, `hooks`, `card_edit` e `cards` operam sobre Postgres (sem fallback JSON).
  - O fluxo de edição público agora vive apenas em `api/routers/card_edit.py`; `cards.py` ficou concentrado nas rotas públicas/QR/vCard/host customizado.
  - As colisões de rota foram removidas: `POST /auth/register` existe apenas em `auth.py`, e `GET/POST /edit/{slug}` existe apenas em `card_edit.py`.
  - Inicializacao: `api.app:create_app()` e `api.admin_app:create_admin_app()`; `api.app_factory` expoe ambos (`public_app`, `admin_app`). Uvicorn: `uvicorn api.app:create_app` (publico) e `uvicorn api.admin_app:create_admin_app` (admin).
  - `api.admin_app.py` usa `SQLRepository` para login/sessoes admin, dashboard, CRUD/reset de cartoes, dominios e usuarios, com paginação/filtros SQL e middleware de segurança compartilhado.
  - Scripts `scripts/add_card.py` e `scripts/reset_card.py` operam no Postgres; `scripts/migrate_to_postgres.py` e utilitario unico para importar JSON legado.

### Status da fase 1 (2026-05-05)
1. **Writes em leitura removidos**:
   - `cards.py` e `card_edit.py` não chamam mais `sync_card_from_json` durante GET/render.
2. **Rotas duplicadas eliminadas**:
   - `POST /auth/register` ficou apenas em `auth.py`; `GET/POST /edit/{slug}` ficou apenas em `card_edit.py`.
3. **Consultas críticas migradas para SQL**:
   - Dashboard/admin/listagens/domínios usam paginação, filtros e agregações em SQL; lookup e conflito de domínio deixaram de depender de full scan em memória.
4. **Segurança operacional reforçada**:
   - `/auth/verify`, reenvios e `/login` do admin passaram a usar rate limit; o admin agora usa middleware compartilhado de headers e `POST /logout` com CSRF.
5. **Migrations e índices versionados**:
   - Baseline Alembic em `alembic/versions/20260505_0001_baseline_schema.py`, com índices para os campos mais consultados.
6. **Front consolidado no básico**:
   - Onboarding, login, slug select, confirmação de e-mail e edição passaram a reutilizar CSS compartilhado em `web/card.css`; `templates/base.html` usa `css_href` fingerprintado.
7. **Backlog real remanescente**:
   - Worker/KV ainda sem sincronização Postgres → edge.
   - Sem `healthz`/`readyz`, logs estruturados, auditoria, CI de PR e deploy do Worker no pipeline.
   - `README.md`, `CHANGELOG.md` e `db/schema.sql` seguem defasados em relação ao runtime.

### Proximas etapas
1. **Fechar o loop Postgres → edge**: criar projeção/sincronização de `cards` para KV `CARDS` a cada mutação relevante (create, activate, block, reset, slug, custom-domain) e definir estratégia de invalidação/cache no Worker.
2. **Observabilidade e operação**: adicionar `healthz`/`readyz`, logs estruturados com request-id, auditoria mínima (login, reset/bloqueio, custom-domain) e métricas.
3. **Segurança de produção**: trocar o rate limit in-memory por backend distribuído, evoluir admin para 2FA/TOTP e revisar quotas/limpeza de uploads.
4. **Pipeline/infra**: CI com lint+pytest, ambiente dev reproduzível, Docker hardening (usuário não-root, FS read-only, healthcheck), deploy automatizado com migrations + Worker e secrets via secret manager.
5. **Documentação/infra legada**: alinhar `README.md`, `CHANGELOG.md` e `db/schema.sql` ao runtime real ou aposentá-los em favor de migrations/documentação nova.
