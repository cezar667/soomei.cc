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
  - `uvicorn api.admin_app:app --reload --port 8001`
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
- Rate limiting: `api.core.rate_limiter.rate_limit_ip` cobre slug-check/update, login/register/forgot e custom-domain; falta proteger `/auth/verify`/reenvios e `/login` do admin. Além disso, o limiter atual é in-memory por processo e não escala para múltiplos workers/instâncias.
- Admin: `api.admin_app` ainda não reutiliza o middleware de headers de segurança da app pública e mantém `GET /logout`; antes de produção, preferir POST + CSRF e alinhar CSP/XFO/HSTS entre apps.
- Auditoria/Observabilidade: Sentry/OTel para erros; logs estruturados com correlação (request-id) ainda pendentes.
- Segredos: variáveis de ambiente/secret manager. Nunca commitar chaves (KV/Queue IDs reais).

## Performance
- Server: Uvicorn com `--http h11 --loop uvloop` (Linux), workers por CPU atrás de proxy (NGINX/Cloudflare).
- Serialização: `orjson` para respostas JSON pesadas; compressão gzip/brotli no proxy.
- Cache:
  - CDN para assets estáticos (`web/`)
  - ETag/Last-Modified nas páginas estáticas simples
  - Worker: considerar caches curtíssimos para redirects `/{uid}` quando `vanity` estável (invalidação por evento)
- Banco: além de PK/unique do ORM, faltam índices explícitos para consultas frequentes (`cards.owner_email`, `cards.status`, `sessions.expires_at`, `verify_tokens.email`, `reset_tokens.email`, `custom_domains.card_uid`). Hoje há filtragem em memória em partes do admin e das checagens de domínio.
- Métricas: `metrics_views` é incrementado de forma síncrona a cada visita; para volume real, mover coleta para fila/eventos e agregar de forma assíncrona.
- Não bloquear loop: operações pesadas (QR, vCard com foto, imagem) ainda acontecem durante requests; o fluxo de upload já usa `asyncio.to_thread` em `card_edit`, mas a geração pública continua síncrona e deve ser cacheada/precomputada quando houver tráfego maior.
- Estado atual crítico: `sync_card_from_json` ainda é chamado em caminhos de leitura de `cards`/`card_edit`; como o helper persiste no SQL, isso gera write amplification e atualiza `updated_at` em acessos públicos.

## Dados e Evolução para Produção
- Fonte de dados: Postgres (`users`, `cards`, `profiles`, `sessions`, `sessions_admin`, `verify_tokens`, `reset_tokens`, `custom_domains`) via `api.db.models`/`create_tables`. Alembic/migrations versionadas ainda pendentes.
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
- [ ] Rate limit em `/login` do admin.
- [ ] 2FA (TOTP) para contas admin (futuro).

Checklist de Segurança em Produção
- [x] HTTPS forçado (HSTS via proxy) — o middleware de segurança injeta `Strict-Transport-Security` automaticamente quando `APP_ENV=prod`; basta manter o app atrás de HTTPS real/proxy.
- [x] Cookies `Secure; HttpOnly; SameSite=Lax`/`Strict` (cookies de sessão/admin já aplicam `HttpOnly` + `SameSite=Strict` e habilitam `Secure` quando `APP_ENV=prod`; o cookie de CSRF segue exposto por design).
- [x] CSRF em endpoints state-changing (FastAPI usa `api.core.csrf` com cookie + header e validação de origem).
- [ ] Rate limiting completo (login/register/forgot e slug/custom-domain já limitados; falta `/auth/verify`, reenvios e `/login` do admin).
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
  - Postgres em `api.db` (`session.py`, `models.py`, `create_tables.py`). `SQLRepository` cobre CRUD de usuarios/cards/perfis/tokens/sessoes/dominios; `sync_card_from_json` ficou so para import/backfill (rotas `cards`/`card_edit` ainda chamam, mas a fonte e o SQL).
- Dominio/Servicos:
  - `api.domain.slugs` segue com regras/reservados; `api.services.slug_service` usa apenas o repositorio SQL.
  - `api.services.card_service` resolve cards direto no banco; helpers de sync so para legado.
  - `api.services.session_service` usa tabela `sessions`; `api.services.auth_service` usa Postgres para registro/login/verificacao/reset.
  - `api.services.custom_domain_service`/`domain_service` consultam somente SQL (`custom_domains` + meta JSON no card).
- Core cross-cutting: `api.core.config`, `security` (argon2), `csrf`, `rate_limiter`, `mailer` e `utils`.
- Routers FastAPI:
  - `auth`, `slug`, `custom_domain`, `pages`, `hooks`, `card_edit` e `cards` operam sobre Postgres (sem fallback JSON); `cards`/`card_edit` ainda chamam `sync_card_from_json` em caminhos de leitura e precisam parar de fazer persistência durante GET/render.
  - `card_edit.py` e `cards.py` mantêm duplicação relevante de fluxo `/edit`, upload, HTML e custom-domain; antes de crescer novas features, consolidar em um único módulo/use-case.
  - Há colisões de rota que hoje funcionam por ordem de inclusão, não por desenho explícito: `POST /auth/register` aparece em `pages.py` e `auth.py`; `/edit/{slug}` está implementado em dois routers.
  - Inicializacao: `api.app:create_app()` e `api.admin_app:create_admin_app()`; `api.app_factory` expoe ambos (`public_app`, `admin_app`). Uvicorn: `uvicorn api.app:create_app` (publico) e `uvicorn api.admin_app:create_admin_app` (admin).
  - `api.admin_app.py` usa `SQLRepository` para login/sessoes admin, dashboard, CRUD/reset de cartoes, dominios e usuarios.
  - Scripts `scripts/add_card.py` e `scripts/reset_card.py` operam no Postgres; `scripts/migrate_to_postgres.py` e utilitario unico para importar JSON legado.

### Lacunas confirmadas na auditoria (2026-05-04)
1. **Writes em leitura**:
   - `api/routers/cards.py` e `api/routers/card_edit.py` ainda chamam `SQLRepository.sync_card_from_json` ao carregar cartão. Como o helper faz `commit`, cada visita pública pode virar escrita no banco.
2. **Fonte de verdade duplicada no edge**:
   - O Worker usa KV `CARDS`, mas não existe rotina oficial que publique no KV as mudanças feitas no Postgres (ativação, bloqueio, reset, slug, owner, domínio).
3. **Escalabilidade limitada por full scan**:
   - Admin, checagens de custom-domain e fallback de lookup por host ainda carregam cartões em memória e filtram em Python. Isso não sustenta crescimento de base.
4. **Sobreposição de rotas e código duplicado**:
   - `cards.py` e `card_edit.py` repetem HTML/JS/upload; `pages.py` registra rota duplicada para `/auth/register`. Isso aumenta risco de regressão e comportamento implícito.
5. **Prontidão operacional incompleta**:
   - Sem Alembic, health/readyz, logs estruturados, CI de PR, Dockerfile, deploy do Worker no pipeline e cobertura de testes para rotas críticas.
6. **Documentação defasada**:
   - `README.md`, `CHANGELOG.md` e `db/schema.sql` não refletem integralmente o runtime atual e precisam ser tratados como backlog de documentação/infra.

### Proximas etapas
1. **Corrigir inconsistencias de runtime**: remover `sync_card_from_json` de leituras, eliminar rotas duplicadas, unificar `card_edit.py`/`cards.py` no fluxo de edição e revisar `db/schema.sql`/docs para o estado real.
2. **Fechar o loop Postgres → edge**: criar projeção/sincronização de `cards` para KV `CARDS` a cada mutação relevante (create, activate, block, reset, slug, custom-domain) e definir estratégia de invalidação/cache no Worker.
3. **Escalar consultas e dados**: Alembic/migrations versionadas, índices reais, paginação e filtros SQL no admin, remoção de full scans em domínio customizado, quotas/limpeza para uploads e desenho de métricas assíncronas.
4. **Segurança e operação**: rate limit distribuído para auth/admin, auditoria mínima (login, reset/bloqueio, custom-domain), 2FA/TOTP no admin, headers de segurança também no admin, logout via POST, logging estruturado com request-id, health/readyz e métricas.
5. **Pipeline/infra**: CI com lint+pytest, ambiente dev reproduzível, Docker hardening (usuário não-root, FS read-only, healthcheck), deploy automatizado com migrations + Worker e secrets via secret manager.
