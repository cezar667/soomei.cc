# INSTRUCTION PARA IMPLEMENTAÇÃO DO MÓDULO DE INDICAÇÕES E RECOMPENSAS — SOOMEI CARDS

## 1. Contexto do produto

A Soomei possui um ecossistema principal de associação, e o cartão digital/NFC é um benefício entregue aos associados.

O fluxo comercial principal não acontece necessariamente dentro do portal de cartões. Um novo cliente pode conhecer a Soomei por uma indicação, fazer a assinatura no site/plataforma comercial da Soomei e somente depois receber um cartão digital para ativação.

Por isso, o vínculo de indicação não deve ser assumido no momento da compra. Ele deve ser registrado no momento em que o novo cliente ativa o cartão e informa o código de indicação de quem o convidou.

O módulo de indicações deve incentivar membros atuais a convidarem novos associados por meio de um código promocional individual, acumulando benefícios visuais e promocionais.

---

## 2. Objetivo da feature

Criar um módulo de indicação/recompensa para:

* gerar um código único de indicação para cada perfil elegível;
* permitir que o dono do cartão compartilhe uma mensagem pronta de convite;
* permitir que um novo associado informe um código de indicação durante a ativação do cartão;
* validar o código informado;
* registrar a relação entre indicador e indicado;
* premiar indicador e indicado após a ativação bem-sucedida;
* exibir benefícios ativos no perfil/cartão;
* acumular dias de selo visual conforme novas indicações forem convertidas;
* registrar cupons promocionais para campanhas como “Pix da Virada”;
* permitir acompanhamento pelo painel admin.

O módulo deve ser flexível para suportar novas campanhas e recompensas no futuro, sem reescrever a regra principal.

---

## 3. Conceito de produto

## 3.1. Código de indicação

Cada conta/cartão ativo pode possuir um código promocional único.

Exemplos:

```text
CEZAR2026
SOOMEI-8F4K2
CEZAR-DAMASCENO
```

Recomendação inicial:

* gerar automaticamente um código curto, legível e único;
* permitir edição futura pelo admin, não necessariamente pelo usuário no MVP;
* armazenar o código em caixa alta;
* remover acentos, espaços e caracteres ambíguos;
* bloquear códigos reservados, ofensivos ou parecidos com termos oficiais sensíveis.

## 3.2. Recompensa visual principal

Quando um código é usado com sucesso na ativação de um novo cartão:

* o perfil indicador ganha mais 30 dias de selo visual;
* o perfil indicado ganha 30 dias de selo visual;
* se o perfil já possuir selo ativo, os novos dias são acumulados a partir da data de expiração atual;
* se o selo estiver expirado, os novos dias contam a partir da data atual.

Nome recomendado para o selo:

```text
Selo Conector Soomei
```

Evitar chamar de “selo verificado” se ele não representar uma validação oficial de identidade, pois isso pode gerar expectativa jurídica/comercial indevida.

Exemplos de textos:

```text
Conector Soomei
Perfil em destaque por indicações qualificadas.
```

```text
Este perfil faz parte da rede de conexões Soomei.
```

## 3.3. Benefício promocional adicional

Além do selo, cada indicação qualificada pode gerar cupons para campanhas promocionais.

Campanha sugerida:

```text
Pix da Virada
```

Regra inicial sugerida:

* cada indicação convertida gera 1 cupom para o indicador;
* opcionalmente, gerar 1 cupom também para o indicado como bônus de boas-vindas;
* cupons ficam vinculados a uma campanha ativa;
* o admin pode consultar participantes, quantidade de cupons e origem de cada cupom.

Observação jurídica importante:

Campanhas com sorteio, prêmio em dinheiro, Pix ou vantagem econômica podem exigir regulamento específico, critérios claros, auditoria, elegibilidade, política antifraude e avaliação jurídica/contábil. O sistema deve armazenar dados suficientes para rastreabilidade, mas o texto final do regulamento deve ser validado por especialista jurídico.

---

## 4. Fluxo funcional esperado

## 4.1. Cliente A indica a Soomei

1. Cliente A acessa a tela de edição/gestão do cartão.
2. Encontra uma seção chamada “Indique e ganhe” ou “Conecte e ganhe”.
3. Visualiza seu código promocional.
4. Clica em “Compartilhar convite”.
5. O sistema gera uma mensagem pronta com link para o site da Soomei e o código de indicação.

Mensagem sugerida:

```text
Conheça a Soomei e faça parte da nossa rede de conexões.

Use meu código de indicação: CEZAR2026

Depois de se associar e ativar seu cartão Soomei, informe esse código para liberar benefícios para você e para mim.

Acesse: https://soomei.com.br
```

No mobile, usar Web Share API quando disponível.

Fallback:

* copiar mensagem para área de transferência;
* abrir WhatsApp com texto preenchido;
* exibir botão “Copiar código”.

## 4.2. Cliente B recebe a indicação

1. Cliente B recebe a mensagem.
2. Acessa o site principal da Soomei.
3. Realiza a assinatura/associação por lá.
4. A Soomei emite um cartão para Cliente B.
5. O cartão ainda não possui vínculo de indicação.

## 4.3. Cliente B ativa o cartão

1. Cliente B acessa o fluxo de ativação do cartão.
2. Informa dados obrigatórios do onboarding.
3. Opcionalmente informa o código de indicação.
4. O backend valida o código.
5. Se válido, registra a indicação como qualificada após a ativação.
6. O sistema concede recompensas.

## 4.4. Resultado da ativação com indicação válida

* Cliente A recebe mais 30 dias de Selo Conector Soomei.
* Cliente B recebe 30 dias de Selo Conector Soomei.
* Cliente A recebe cupom da campanha ativa, se houver.
* Cliente B pode receber cupom de boas-vindas, se a campanha definir.
* O admin consegue auditar quem indicou quem, quando e quais recompensas foram concedidas.

---

## 5. Regras de negócio obrigatórias

## 5.1. Elegibilidade do indicador

Um código de indicação deve ser aceito somente se:

* existir;
* estiver ativo;
* pertencer a um cartão/usuário elegível;
* não pertencer ao próprio cartão em ativação;
* não estiver bloqueado por suspeita de abuso;
* não tiver excedido limites configurados, se houver.

## 5.2. Prevenção de autoindicação

O sistema deve impedir autoindicação por:

* mesmo card UID;
* mesmo e-mail;
* mesmo usuário;
* opcionalmente mesmo documento/telefone, se esses dados existirem no domínio.

Se houver dúvida, registrar tentativa como inválida/suspeita e não conceder recompensa automaticamente.

## 5.3. Uma indicação por cartão indicado

Cada cartão indicado deve poder ter no máximo uma indicação qualificada.

Se Cliente B já ativou com um código, não deve conseguir trocar para outro depois.

## 5.4. Código inválido

Se o código informado for inválido:

* não deve impedir a ativação do cartão, exceto se o negócio decidir o contrário;
* deve exibir uma mensagem clara:

```text
Código de indicação não encontrado. Você pode continuar a ativação sem indicação.
```

Recomendação de UX:

* permitir continuar sem código;
* não perder os dados já preenchidos;
* não revelar informações sensíveis sobre o dono do código quando inválido.

## 5.5. Idempotência

O processamento de recompensa deve ser idempotente.

Se a ativação for reenviada, recarregada ou processada novamente, o sistema não deve duplicar:

* dias de selo;
* cupons promocionais;
* registros de recompensa.

Usar chaves únicas e transação de banco.

## 5.6. Acúmulo de dias do selo

Regra:

```text
nova_expiracao = max(agora, expiracao_atual) + dias_concedidos
```

Exemplo:

* selo expira em 10/08;
* nova indicação em 01/08;
* ganha +30 dias;
* nova expiração: 09/09.

## 5.7. Auditoria

Toda indicação deve ter registro auditável:

* código usado;
* indicador;
* indicado;
* data;
* IP/user-agent, quando disponível;
* status;
* recompensas emitidas;
* motivo de rejeição, quando houver.

---

## 6. Modelo de dados sugerido

As mudanças de schema devem ser feitas com Alembic incremental.

Não usar `create_all()` como fonte de evolução de produção.

## 6.1. `referral_codes`

Tabela para os códigos de indicação.

Campos sugeridos:

```text
id UUID/serial
code VARCHAR unique not null
owner_card_uid VARCHAR not null
owner_email VARCHAR
status VARCHAR not null default 'active'
created_at TIMESTAMP not null
updated_at TIMESTAMP
disabled_at TIMESTAMP
disabled_reason TEXT
```

Índices:

```text
unique(code)
index(owner_card_uid)
index(owner_email)
index(status)
```

## 6.2. `referrals`

Tabela principal da relação de indicação.

Campos sugeridos:

```text
id UUID/serial
referral_code_id FK referral_codes.id
code_used VARCHAR not null
referrer_card_uid VARCHAR not null
referrer_email VARCHAR
referred_card_uid VARCHAR not null
referred_email VARCHAR
status VARCHAR not null
qualified_at TIMESTAMP
rejected_at TIMESTAMP
rejection_reason TEXT
source VARCHAR default 'onboarding'
ip_address VARCHAR
user_agent TEXT
created_at TIMESTAMP not null
updated_at TIMESTAMP
```

Constraints:

```text
unique(referred_card_uid)
```

Status sugeridos:

```text
pending
qualified
rejected
cancelled
fraud_review
```

## 6.3. `profile_badges`

Tabela de benefícios visuais por perfil/cartão.

Campos sugeridos:

```text
id UUID/serial
card_uid VARCHAR not null
badge_type VARCHAR not null
label VARCHAR not null
starts_at TIMESTAMP not null
expires_at TIMESTAMP not null
source VARCHAR
source_id VARCHAR
created_at TIMESTAMP not null
updated_at TIMESTAMP
```

Constraints:

```text
unique(card_uid, badge_type)
```

Tipos iniciais:

```text
soomei_connector
```

## 6.4. `referral_campaigns`

Tabela para campanhas promocionais.

Campos sugeridos:

```text
id UUID/serial
slug VARCHAR unique not null
name VARCHAR not null
description TEXT
status VARCHAR not null
starts_at TIMESTAMP
ends_at TIMESTAMP
rules_json JSONB
created_at TIMESTAMP not null
updated_at TIMESTAMP
```

Campanha inicial:

```text
slug: pix-da-virada-2026
name: Pix da Virada
status: active
```

## 6.5. `referral_rewards`

Tabela de recompensas concedidas.

Campos sugeridos:

```text
id UUID/serial
referral_id FK referrals.id
campaign_id FK referral_campaigns.id nullable
beneficiary_card_uid VARCHAR not null
beneficiary_email VARCHAR
reward_type VARCHAR not null
quantity INTEGER not null default 1
metadata_json JSONB
status VARCHAR not null default 'granted'
granted_at TIMESTAMP not null
revoked_at TIMESTAMP
revoked_reason TEXT
created_at TIMESTAMP not null
```

Tipos iniciais:

```text
badge_days
raffle_coupon
```

Constraints de idempotência:

```text
unique(referral_id, beneficiary_card_uid, reward_type, campaign_id)
```

## 6.6. `raffle_entries`

Tabela específica para cupons de sorteio, se for necessário consultar sorteios com performance e clareza.

Campos sugeridos:

```text
id UUID/serial
campaign_id FK referral_campaigns.id
reward_id FK referral_rewards.id
card_uid VARCHAR not null
email VARCHAR
entry_code VARCHAR unique not null
status VARCHAR not null default 'active'
created_at TIMESTAMP not null
cancelled_at TIMESTAMP
cancelled_reason TEXT
```

---

## 7. Arquitetura sugerida

Seguir o padrão atual do projeto:

```text
api/
├── referrals/
│   ├── enums.py
│   ├── repository.py
│   ├── schemas.py
│   ├── service.py
│   └── router.py
├── db/
│   └── models.py
├── routers/
│   ├── auth.py
│   ├── card_edit.py
│   └── cards.py
└── admin_app.py
```

Responsabilidades:

* router: receber entrada, validar CSRF/sessão quando aplicável e chamar serviço;
* service: regras de negócio, idempotência, concessão de recompensas;
* repository: queries SQLAlchemy;
* models: tabelas e índices;
* admin_app: telas administrativas e ações internas.

Não misturar a regra de indicação diretamente no HTML gerado por `auth.py` ou `card_edit.py`.

---

## 8. Pontos de integração no sistema atual

## 8.1. Onboarding / ativação

No formulário de ativação, adicionar campo opcional:

```text
Código de indicação
```

Texto de apoio:

```text
Recebeu um convite? Informe o código para liberar benefícios para você e para quem te indicou.
```

No `POST /auth/register`:

1. validar dados atuais normalmente;
2. se houver código, normalizar e validar;
3. concluir ativação em transação;
4. registrar referral;
5. conceder recompensas;
6. seguir fluxo atual de confirmação/e-mail sem quebrar rotas.

## 8.2. Tela de edição/gestão do cartão

Adicionar bloco:

```text
Conecte e ganhe
```

Conteúdos:

* código de indicação do usuário;
* botão copiar código;
* botão compartilhar convite;
* dias restantes do Selo Conector;
* total de indicações qualificadas;
* cupons ativos em campanhas;
* lista resumida de benefícios disponíveis.

Exemplo de microcopy:

```text
Convide novos associados para a Soomei. Quando alguém ativar o cartão usando seu código, vocês dois ganham benefícios.
```

## 8.3. Tela pública do cartão

Se o cartão possuir selo ativo:

* exibir selo visual discreto e premium;
* não poluir a primeira dobra;
* manter coerência com o design já criado.

Texto curto sugerido:

```text
Conector Soomei
```

Tooltip/explicação opcional:

```text
Perfil em destaque por conexões qualificadas na rede Soomei.
```

## 8.4. Admin

Adicionar telas úteis:

```text
/referrals
/referrals/codes
/referrals/campaigns
/referrals/rewards
```

Funcionalidades:

* listar indicações;
* filtrar por status, código, indicador, indicado e período;
* visualizar detalhe da indicação;
* ver recompensas concedidas;
* cancelar/revogar recompensa, se necessário;
* bloquear código;
* criar/editar campanhas;
* consultar ranking de indicadores;
* consultar participantes do Pix da Virada.

---

## 9. UX e design

## 9.1. Tom de comunicação

Usar linguagem simples, positiva e direta.

Evitar termos que soem financeiros demais ou prometam ganho garantido.

Preferir:

```text
Convide alguém para a rede Soomei.
```

```text
Quando a pessoa ativar o cartão usando seu código, vocês liberam benefícios.
```

Evitar:

```text
Ganhe dinheiro indicando.
```

```text
Indicação premiada garantida.
```

## 9.2. Tela de indicação no perfil

Elementos recomendados:

* card destacado com código;
* botão primário de compartilhar;
* botão secundário de copiar;
* card de “Benefícios ativos”;
* card de “Como funciona” em 3 passos;
* lista de campanhas ativas;
* selo visual caso esteja ativo.

## 9.3. Tela de onboarding

O campo de código deve ser opcional e não deve parecer uma barreira.

Layout sugerido:

```text
Tem um código de indicação?
[ Código ]

Ao ativar com um código válido, você e quem te indicou podem receber benefícios Soomei.
```

---

## 10. Segurança e antifraude

Validar no mínimo:

* código existente e ativo;
* indicador diferente do indicado;
* indicação única por cartão indicado;
* e-mail do indicado diferente do indicador;
* cartão indicador não bloqueado;
* campanha ativa no momento da recompensa;
* transação única para referral + rewards;
* logs de IP e user-agent.

Backlog antifraude:

* limite de indicações por período;
* flag para revisão manual;
* score por padrão suspeito;
* bloquear recompensa em caso de chargeback/cancelamento precoce;
* revogar cupons se assinatura for cancelada dentro de janela definida.

---

## 11. Aspectos jurídicos e regulatórios

Este documento não substitui parecer jurídico.

Para reduzir risco:

* publicar regulamento claro da campanha;
* deixar explícito que benefícios dependem de ativação válida;
* prever desclassificação por fraude, abuso ou cadastro irregular;
* definir período da campanha;
* definir quem é elegível;
* definir quantidade e natureza dos prêmios;
* armazenar trilha auditável de cupons;
* evitar promessas de prêmio garantido quando houver sorteio;
* obter aceite de termos atualizados se necessário;
* revisar LGPD para tratamento de dados usados em indicação e campanhas.

Texto base para regras:

```text
Os benefícios por indicação são concedidos conforme regras vigentes da campanha e podem ser suspensos, cancelados ou revogados em caso de uso indevido, fraude, inconsistência cadastral, cancelamento da associação ou descumprimento dos termos da Soomei.
```

---

## 12. Testes obrigatórios

Criar testes para:

* geração de código único;
* normalização de código;
* validação de código inexistente;
* rejeição de autoindicação;
* rejeição de cartão já indicado;
* ativação com código válido;
* concessão de +30 dias para indicador;
* concessão de +30 dias para indicado;
* acúmulo de dias quando selo já está ativo;
* não duplicar recompensa em reprocessamento;
* geração de cupom para campanha ativa;
* não gerar cupom quando campanha inativa;
* exibição do selo apenas quando ativo;
* tela de edição exibindo código/benefícios;
* admin listando indicações/recompensas.

---

## 13. Critérios de aceite do MVP

O MVP estará pronto quando:

* cada cartão elegível tiver um código de indicação único;
* a tela de edição mostrar o código e permitir compartilhar;
* o onboarding aceitar código opcional;
* código válido gerar uma indicação qualificada;
* indicador e indicado receberem selo por 30 dias;
* dias de selo forem acumulados corretamente;
* uma campanha ativa puder gerar cupom;
* admin puder visualizar indicações e recompensas;
* a feature tiver migration Alembic;
* a feature tiver testes automatizados;
* a feature não quebrar rotas existentes;
* a feature não depender de `data.json`.

---

## 14. Backlog futuro

* link de indicação com captura automática de código;
* landing page `/indique/{code}`;
* ranking público ou privado de conectores;
* níveis de selo por volume de indicações;
* recompensas configuráveis pelo admin;
* expiração de cupons;
* sorteio auditável com exportação CSV;
* painel do usuário com histórico completo;
* integração com e-mail/WhatsApp para confirmação da indicação;
* antifraude avançado;
* revogação automática por cancelamento/chargeback;
* métricas de conversão por origem.

---

## 15. Ordem de implementação recomendada

1. Criar modelos e migration Alembic.
2. Criar repository/service de referrals.
3. Gerar códigos para cartões existentes via migration/script controlado.
4. Adicionar campo opcional no onboarding.
5. Processar indicação na ativação.
6. Implementar selo no perfil público.
7. Implementar seção “Conecte e ganhe” na edição.
8. Implementar campanha inicial “Pix da Virada”.
9. Implementar admin de indicações/recompensas.
10. Adicionar testes.
11. Atualizar README/docs.

---

## 16. Decisões iniciais recomendadas

* Nome do selo: **Selo Conector Soomei**.
* Benefício inicial: **30 dias de selo para indicador e indicado**.
* Acúmulo: **sim, acumulativo por indicação qualificada**.
* Cupom Pix da Virada: **1 cupom para o indicador por indicação qualificada**.
* Campo de indicação: **opcional no onboarding**.
* Uma indicação por cartão indicado: **sim**.
* Admin pode revogar benefício: **sim**.
* Usuário pode editar código no MVP: **não; deixar para admin/futuro**.

