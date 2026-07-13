# Postman — Soomei Membership Webhooks

Arquivos:

- `soomei-membership-webhooks.postman_collection.json`
- `soomei-themembers-webhooks.postman_collection.json`
- `soomei-referrals-rewards.postman_collection.json`
- `soomei-local.postman_environment.json`

## Como usar

1. Importe a collection no Postman do VSCode.
2. Importe o environment `Soomei Local`.
3. Confira as variáveis:

```text
base_url=http://127.0.0.1:8000
webhook_secret=segredo-teste-123
themembers_token=segredo-teste-123
```

## Collection genérica com HMAC

Use `Soomei - Membership Webhooks` para validar o contrato genérico interno com HMAC.

Suba a API com:

```bash
export MEMBERSHIP_WEBHOOK_ENABLED=true
export MEMBERSHIP_WEBHOOK_SECRET=segredo-teste-123
export MEMBERSHIP_WEBHOOK_PROVIDER=membership_platform

uvicorn api.app:create_app --factory --reload --port 8000
```

Rode primeiro:

```text
00 - Preparar rodada / Gerar novo run_id
```

Depois rode os cenários na ordem:

```text
01 - Fluxo principal da assinatura
02 - Eventos adicionais
03 - Segurança e payload inválido
```

## Collection TheMembers real

Use `Soomei - TheMembers Webhooks` para validar o formato nativo da TheMembers.

Suba a API com:

```bash
export MEMBERSHIP_WEBHOOK_ENABLED=true
export MEMBERSHIP_WEBHOOK_PROVIDER=themembers
export MEMBERSHIP_WEBHOOK_SECRET=segredo-teste-123

uvicorn api.app:create_app --factory --reload --port 8000
```

No caso TheMembers, `MEMBERSHIP_WEBHOOK_SECRET` é comparado com o campo `token` enviado no payload. A collection usa a variável:

```text
themembers_token=segredo-teste-123
```

Rode primeiro:

```text
00 - Preparar rodada / Gerar novo run_id
```

Depois rode:

```text
01 - Fluxo recomendado por acesso
02 - Transacionais TheMembers
03 - Segurança e compatibilidade
```

## O que a collection valida

- Assinatura HMAC-SHA256 automática usando o corpo bruto.
- Pagamento aprovado criando associado, assinatura e cartão pendente.
- Idempotência com evento duplicado.
- Inadimplência bloqueando cartão.
- Regularização reativando cartão quando a suspensão foi por pagamento.
- Cancelamento, estorno e chargeback.
- Evento desconhecido persistido como ignorado.
- Assinatura inválida.
- Timestamp expirado.
- Payload alterado após assinatura.
- Payload inválido.
- Payload TheMembers com token no corpo.

## Collection de indicações e recompensas

Use `Soomei - Indicações e Recompensas` para simular o fluxo em que um cliente indica outro. No momento da ativação do cartão do indicado, o indicador recebe o benefício visual **Destaque Soomei** e os dois recebem cupom de participação da campanha **Pix da Virada**.

Suba a API pública normalmente:

```bash
export APP_ENV=dev
export PUBLIC_BASE_URL=http://127.0.0.1:8000
export DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/soomei

uvicorn api.app:create_app --factory --reload --port 8000
```

Antes de executar o fluxo principal, rode no Postman:

```text
00 - Preparar rodada / Gerar dados únicos da rodada
```

Esse request gera variáveis como:

```text
referrer_uid
referrer_pin
referred_uid
referred_pin
referrer_email
referred_email
referrer_slug
referred_slug
```

Depois crie os dois cartões pendentes no terminal usando os valores gerados. O próprio Postman imprime os comandos no console, mas o formato é:

```bash
py scripts/add_card.py --uid <referrer_uid> --pin <referrer_pin>
py scripts/add_card.py --uid <referred_uid> --pin <referred_pin>
```

Em seguida rode, em ordem:

```text
02 - Ativar indicador A e capturar código
03 - Ativar indicado B usando o código
04 - Validar recompensa visual
```

O fluxo faz automaticamente:

- abertura do onboarding;
- captura do `csrf_token`;
- cadastro do indicador;
- confirmação de e-mail em ambiente dev;
- abertura da tela de edição do indicador;
- captura do código de indicação;
- cadastro do indicado usando o código;
- confirmação de e-mail do indicado;
- validação da seção de benefícios e do selo clicável no cartão público.

### Opção via webhook

A pasta `01 - Opcional: criar cartões via webhook` dispara eventos de pagamento aprovado para indicador e indicado. Ela é útil para validar a criação de cartões pelo módulo de webhooks, mas o UID/PIN são gerados aleatoriamente. Para continuar o onboarding pelo Postman, consulte o banco e copie os valores para as variáveis `referrer_uid`, `referrer_pin`, `referred_uid` e `referred_pin`.

Consulta útil:

```sql
select uid, pin, owner_email, external_subscription_id, status
from cards
where external_subscription_id in (
  'subscription_referrer_<run_id>',
  'subscription_referred_<run_id>'
)
order by created_at desc;
```

## Conferência no banco

```sql
select external_event_id, event_type, status, attempts
from webhook_events
order by received_at desc;

select provider, external_customer_id, email, name
from members
order by created_at desc;

select external_subscription_id, status
from external_subscriptions
order by created_at desc;

select uid, status, status_reason, owner_email, external_subscription_id
from cards
where external_subscription_id like 'subscription_postman_%'
order by created_at desc;

select card_uid, previous_status, new_status, reason, source, external_event_id
from card_status_history
order by created_at desc;
```
