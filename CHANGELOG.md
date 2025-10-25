# Changelog

Todas as mudanças notáveis do projeto serão documentadas aqui.

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

