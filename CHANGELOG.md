# Changelog

Todas as mudanças notáveis do projeto serão documentadas aqui.

## [0.2.0] - 2025-10-27 — User & Admin Improvements

### Added
- **Login com Google** (OAuth) na autenticação.
- **Validação de e-mail em produção** (envio de token e confirmação).
- **Endpoints de integração** para eventos externos:
  - novo usuário, registro de pagamento, bloqueio e outros status.
- **Admin: reset de senha** para padrão temporário.
- **Admin: importação de tags por planilha** (criação em massa).
- **Página `/invalid`** para rotas/slug inválidos, com input de slug e botão “Ir”.
- **Visualização de perfil no admin** (dados completos).
- **Máscara & normalização de telefone** (formato E.164) para links do WhatsApp.
- **Integração de Pixel Ads** (Google/Facebook) configurável.

### Changed
- **Campo de cadastro**: “Nome completo” → **“Nome”**.
- **Edição de perfil**: agora permite **alterar o slug** com validação.
- **Responsividade mobile**: reduzido zoom automático ao focar inputs.
- **Versionamento de CSS**: inclusão de hash/param de versão para bust de cache.

### Fixed
- **Foto de perfil**: erro ao trocar múltiplas vezes corrigido (armazenamento dedicado, hash no nome, invalidação de cache).
- **Acesso sem slug**: remove JSON cru `{"detail":"Not Found"}` e redireciona para `/invalid`.

### Tech Notes / Migration
- DB:
  - Adicionar coluna `google_id` (nullable) em `members` para OAuth.
  - (Opcional) Tabela/log para importações de tags.
- Infra:
  - Limpar caches de assets antigos após deploy.
  - Garantir variáveis de ambiente para provedores de e-mail e OAuth.
- Segurança:
  - Normalizar e armazenar telefones em E.164 (ex.: `+55DDDNÚMERO`).
  - Rate-limit básico para tentativas de slug na página `/invalid`.

### Deployment Checklist
- [ ] Executar migrações de banco (ex.: `add_google_id.sql`).
- [ ] Configurar credenciais OAuth Google (client ID/secret) em PROD.
- [ ] Configurar serviço/credenciais de e-mail de verificação.
- [ ] Publicar assets com versão (ex.: `main.css?v=0.2.0`) e invalidar CDN.
- [ ] Validar webhooks de eventos (novo usuário, pagamento, bloqueio) em sandbox.
- [ ] Smoke tests: login (senha e Google), cadastro + verificação de e-mail, troca de foto, edição de slug, WhatsApp com número normalizado, página `/invalid`.

---

## [Unreleased]

- ...


## [v0.1.1] - 2025-10-26
- Infra de releases adicionada (GitHub Actions) para criar Release ao publicar tags `v*`.
- CHANGELOG inicial criado e versão documentada no README.

## [v0.1.0] - 2025-10-26
- Onboarding:
  - Checkbox estilizado + link para Termos (modal).
  - Validação de slug (live) e verificação de e‑mail apenas no submit.
  - Popup de boas-vindas quando cartão está pendente.
  - Redirecionamentos: ativo → página do cartão, bloqueado → /blocked, inválido → /invalid.
- Termos:
  - Documento legal completo em `legal/terms_v1.md`.
  - Endpoint `/legal/terms` para exibição segura.
- Edição do cartão:
  - Pré-visualização “carbono” com seletor de cor; aplicação suave `#RRGGBB30`.
  - Upload de foto com validações de tipo e tamanho (2 MB), preview instantâneo.
- Página pública:
  - Aplica a cor de tema no card (mantendo textura carbono).
  - Engrenagem de edição discreta no topo direito para o dono.
  - Rodapé: somente “Entrar” (visitante) ou “Sair” (dono). Logout mantém usuário na mesma página.
- Cartão inválido:
  - Card carbono com mensagem amigável de orientação.
- Ajustes gerais de UI/CSS: topbar, ícones (SVG), transições e estrutura.

