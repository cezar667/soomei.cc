# Postman — Soomei Membership Webhooks

Arquivos:

- `soomei-membership-webhooks.postman_collection.json`
- `soomei-themembers-webhooks.postman_collection.json`
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
