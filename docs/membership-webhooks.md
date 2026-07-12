# Webhooks da plataforma externa de associados

Este módulo recebe eventos comerciais da plataforma externa de associação/pagamento e transforma esses eventos em estado interno da Soomei.

O backend Python continua sendo o dono do domínio de cartões. A plataforma externa não deve escrever diretamente no Postgres.

## Endpoint

```http
POST /api/v1/webhooks/membership-platform
```

Em produção, o endpoint deve ser acessado somente via HTTPS.

O endpoint suporta dois formatos conforme `MEMBERSHIP_WEBHOOK_PROVIDER`:

- `membership_platform`: contrato interno genérico com assinatura HMAC por headers.
- `themembers`: payload nativo da TheMembers com token de segurança no corpo.

## Variáveis de ambiente

```text
MEMBERSHIP_WEBHOOK_ENABLED=true
MEMBERSHIP_WEBHOOK_SECRET=
MEMBERSHIP_WEBHOOK_PREVIOUS_SECRET=
MEMBERSHIP_WEBHOOK_MAX_DELAY_SECONDS=300
MEMBERSHIP_WEBHOOK_PROVIDER=membership_platform
MEMBERSHIP_WEBHOOK_RATE_LIMIT_PER_MINUTE=100
MEMBERSHIP_WEBHOOK_MAX_PAYLOAD_BYTES=1048576
MEMBERSHIP_WEBHOOK_MAX_RETRIES=5
MEMBERSHIP_WEBHOOK_WORKER_BATCH_SIZE=50
MEMBERSHIP_WEBHOOK_WORKER_INTERVAL_SECONDS=5
```

Em produção, se `MEMBERSHIP_WEBHOOK_ENABLED=true`, `MEMBERSHIP_WEBHOOK_SECRET` precisa estar configurado.

Para TheMembers:

```text
MEMBERSHIP_WEBHOOK_PROVIDER=themembers
MEMBERSHIP_WEBHOOK_SECRET=<token de segurança configurado na TheMembers>
```

## Banco de dados

Antes de ativar a integração em um banco existente, rode a migration versionada:

```bash
alembic upgrade head
```

A migration `20260712_0002_membership_webhooks` adiciona:

- novas colunas comerciais em `cards`;
- tabela `members`;
- tabela `external_subscriptions`;
- tabela `webhook_events`;
- tabela `card_status_history`;
- índices e constraints de idempotência.

O arquivo `db/migrations/20260712_membership_webhooks.sql` permanece apenas como SQL de referência e apoio ao script legado `scripts/migrate_to_postgres.py`.

## Headers obrigatórios

### Contrato genérico/HMAC

```http
X-Webhook-Timestamp: 1783809000
X-Webhook-Signature: sha256=<hmac>
X-Webhook-Event-Id: evt_987654321
X-Correlation-Id: opcional
```

### TheMembers

A documentação pública da TheMembers descreve um “Token de segurança” gerado/configurado no webhook e normalmente enviado no payload. Nesse modo, o backend compara o campo `token` do payload com `MEMBERSHIP_WEBHOOK_SECRET`.

Headers de HMAC não são obrigatórios no modo `themembers`.

## Assinatura HMAC

A assinatura é:

```text
HMAC-SHA256(secret, timestamp + "." + raw_body)
```

O backend valida usando o corpo bruto recebido. Não assine uma versão reformatada do JSON.

Durante rotação de segredo, o backend tenta validar com:

1. `MEMBERSHIP_WEBHOOK_SECRET`
2. `MEMBERSHIP_WEBHOOK_PREVIOUS_SECRET`

Depois da rotação, remova o segredo anterior.

## Payload esperado

### Contrato genérico interno

```json
{
  "event_id": "evt_987654321",
  "event_type": "subscription.payment_approved",
  "created_at": "2026-07-11T18:30:00Z",
  "data": {
    "customer_id": "customer_123",
    "subscription_id": "subscription_456",
    "order_id": "order_789",
    "product_id": "soomei-card-black",
    "plan_id": "plan_monthly",
    "customer": {
      "name": "João da Silva",
      "email": "joao@email.com",
      "phone": "5534999999999",
      "document": "00000000000"
    }
  }
}
```

Campos mínimos:

- `event_id`
- `event_type`
- `created_at`
- `data.customer_id`
- `data.subscription_id` para eventos de assinatura/pagamento

O e-mail nunca é usado como chave principal da integração. Ele é apenas dado auxiliar para conciliação/conta local.

### TheMembers

A TheMembers envia um payload nativo com campos como:

```json
{
  "id": "wh_1283y17236123",
  "object": "transaction",
  "event": "transaction.failed",
  "created_at": "2019-01-01T00:00:00.002Z",
  "token": "token-configurado-no-webhook",
  "data": {
    "id": "txn_123",
    "paid_at": null,
    "payment_details": {
      "payment_method": "credit_card"
    },
    "transaction": {
      "currency": "brl",
      "amount": 40.0,
      "buyer_fees": 2.33,
      "total_amount": 42.33
    },
    "customer": {
      "id": "customer_123",
      "name": "João da Silva",
      "email": "joao@email.com"
    },
    "product": {
      "id": "soomei-card-black"
    },
    "order": {
      "id": "order_123"
    }
  }
}
```

O adapter TheMembers normaliza:

- `id` → `event_id`
- `event` → `event_type`
- `data.id` → `transaction_id`
- `data.order.id` ou `data.id` → `subscription_id/order_id`
- `data.customer` ou `data.buyer` → dados do associado
- `data.product` ou primeiro item de `data.products` → produto

Como a documentação pública não mostra todos os campos do JSON completo, o adapter é tolerante a variações comuns (`customer`, `buyer`, `client`, `user`, `student`, `contact`).

## Eventos suportados

| Evento externo | Ação interna |
| --- | --- |
| `customer.created` | cria/atualiza associado local |
| `subscription.created` | cria/atualiza associado e assinatura local |
| `subscription.payment_approved` | marca assinatura ativa e cria cartão pendente se ainda não existir |
| `subscription.payment_failed` | marca assinatura como overdue, sem cancelar cartão automaticamente |
| `subscription.overdue` | marca assinatura overdue e bloqueia cartão por motivo `PAYMENT_OVERDUE` |
| `subscription.reactivated` | marca assinatura ativa e reativa somente cartão bloqueado por `PAYMENT_OVERDUE` |
| `subscription.cancelled` | marca assinatura cancelada e bloqueia cartão por cancelamento |
| `payment.refunded` | marca assinatura refunded e bloqueia cartão |
| `payment.chargeback` | marca assinatura suspended e bloqueia cartão |

## Eventos TheMembers mapeados

| Evento TheMembers | Evento interno |
| --- | --- |
| `transaction.approved` | `subscription.payment_approved` |
| `transaction.paid` | `subscription.payment_approved` |
| `transaction.failed` | `subscription.payment_failed` |
| `transaction.denied` | `subscription.payment_failed` |
| `transaction.refunded` | `payment.refunded` |
| `transaction.chargeback` | `payment.chargeback` |
| `order.cancelled` / `order.canceled` | `subscription.cancelled` |
| `order.expired` | `subscription.overdue` |
| `access.granted` / `access.released` | `subscription.payment_approved` |
| `access.removed` / `access.revoked` | `subscription.cancelled` |

Eventos como Pix gerado, boleto gerado, cartão iniciado, abandono de carrinho e eventos futuros são persistidos e marcados como `IGNORED` quando não houver regra interna aplicável.

Eventos desconhecidos são persistidos e marcados como `IGNORED`, retornando sucesso para evitar retentativas infinitas.

## Idempotência

Há duas camadas:

1. `webhook_events` possui unicidade por `(provider, external_event_id)`.
2. `cards` possui unicidade lógica por `(external_provider, external_subscription_id, external_product_id)`.

Se o mesmo evento chegar novamente, o backend retorna sucesso com:

```json
{
  "received": true,
  "event_id": "evt_987654321",
  "duplicate": true
}
```

## Respostas

### Evento aceito

```http
200 OK
```

```json
{
  "received": true,
  "event_id": "evt_987654321",
  "duplicate": false
}
```

### Evento duplicado

```http
200 OK
```

```json
{
  "received": true,
  "event_id": "evt_987654321",
  "duplicate": true
}
```

### Assinatura inválida

```http
401 Unauthorized
```

```json
{
  "detail": "Invalid webhook authentication"
}
```

### Payload inválido

```http
422 Unprocessable Entity
```

### Rate limit

```http
429 Too Many Requests
```

## Processamento e retry

O endpoint registra o evento em `webhook_events` e, no MVP, processa imediatamente para facilitar operação local.

O processamento pendente/retry também pode ser executado por:

```bash
python -m api.integrations.membership_platform.worker
```

Status possíveis:

- `RECEIVED`
- `PROCESSING`
- `PROCESSED`
- `FAILED`
- `RETRY_PENDING`
- `IGNORED`
- `DEAD_LETTER`

## Auditoria

Toda alteração de status do cartão feita pela integração cria registro em `card_status_history` com:

- status anterior;
- novo status;
- motivo;
- fonte `WEBHOOK`;
- `external_event_id`;
- metadados.

## Segurança e LGPD

- Não logar segredos, assinaturas completas, senhas ou tokens.
- Não expor motivo comercial sensível na página pública. Cartões suspensos devem aparecer como indisponíveis/bloqueados de forma genérica.
- Restringir acesso aos payloads persistidos.
- Aplicar política de retenção para payloads brutos em `webhook_events`.

## Retenção e limpeza de payloads

O sistema não deve apagar automaticamente o histórico operacional dos webhooks, pois ele ajuda suporte e auditoria a entender por que um cartão foi criado, suspenso, reativado ou cancelado.

Por outro lado, o payload bruto completo pode conter dados pessoais e não precisa ficar indefinidamente no banco.

Política recomendada:

- payload completo de eventos `PROCESSED`/`IGNORED`: manter por 90 dias;
- payload completo de eventos `FAILED`/`DEAD_LETTER`: manter por 180 dias;
- eventos em `RETRY_PENDING`: não compactar, pois ainda podem precisar de reprocessamento;
- metadados mínimos do evento: manter por mais tempo para auditoria;
- `card_status_history`: manter enquanto for útil para suporte, auditoria e relação com o associado.

Existe um script operacional para compactar payloads antigos sem apagar os eventos:

```bash
python scripts/prune_webhook_events.py
```

Por padrão, ele roda em modo simulação (`dry-run`). Para aplicar:

```bash
python scripts/prune_webhook_events.py --apply
```

Também é possível ajustar a janela:

```bash
python scripts/prune_webhook_events.py --success-days 90 --error-days 180 --limit 500 --apply
```

O payload antigo é substituído por uma versão mínima, sem dados pessoais detalhados, preservando:

- provider;
- event id;
- event type;
- IDs técnicos úteis, como customer/subscription/order/product.

## Homologação rápida

1. Ativar variáveis de ambiente em ambiente local/homologação.
2. Criar payload JSON.
3. Assinar com HMAC usando o corpo bruto.
4. Enviar `POST /api/v1/webhooks/membership-platform`.
5. Conferir:
   - `webhook_events`;
   - `members`;
   - `external_subscriptions`;
   - `cards`;
   - `card_status_history`.

Para homologar TheMembers, importe `postman/soomei-themembers-webhooks.postman_collection.json` e use:

```bash
export MEMBERSHIP_WEBHOOK_ENABLED=true
export MEMBERSHIP_WEBHOOK_PROVIDER=themembers
export MEMBERSHIP_WEBHOOK_SECRET=segredo-teste-123
```
