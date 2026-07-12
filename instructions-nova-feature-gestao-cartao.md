# INSTRUCTION PARA IMPLEMENTAÇÃO DA INTEGRAÇÃO DE WEBHOOKS — SOOMEI CARDS

## 1. Contexto do projeto

O sistema Soomei possui um portal de cartões digitais e cartões NFC.

O backend atual do portal está desenvolvido em Python e utiliza PostgreSQL como banco de dados.

O processo de cadastro, contratação, pagamento, renovação, inadimplência e cancelamento da associação acontece em uma plataforma externa pertencente a outra empresa.

Essa plataforma externa permite configurar webhooks.

Sempre que ocorrer um evento comercial relevante, como pagamento aprovado, assinatura criada, inadimplência, regularização ou cancelamento, a plataforma externa enviará uma requisição HTTP para o backend da Soomei.

O backend Python da Soomei deverá:

* receber o webhook;
* validar a autenticidade da requisição;
* impedir processamento duplicado;
* registrar o evento recebido;
* interpretar o evento;
* criar ou atualizar o associado;
* criar, ativar, suspender, reativar ou cancelar o cartão;
* registrar histórico e auditoria;
* manter o portal transparente à plataforma externa.

O portal de cartões não deve conhecer diretamente a plataforma externa.

O portal deve apenas consultar as APIs do backend Python e refletir o estado atual persistido no PostgreSQL.

---

# 2. Objetivo da implementação

Implementar um módulo robusto de integração por webhooks dentro do backend Python existente.

A integração deverá ser segura, idempotente, auditável, resiliente e preparada para crescimento futuro.

Não criar um novo microsserviço em Java.

Não criar um segundo backend apenas para receber os webhooks.

Não permitir que outro serviço altere diretamente as tabelas de cartões.

O backend Python existente deve continuar sendo o proprietário do domínio de cartões.

---

# 3. Princípios arquiteturais obrigatórios

## 3.1. O backend Python é o dono do domínio de cartões

Somente o backend Python deve:

* criar cartões;
* atualizar cartões;
* suspender cartões;
* reativar cartões;
* cancelar cartões;
* alterar o perfil do cartão;
* registrar histórico de status;
* persistir eventos externos.

Nenhum serviço externo deve escrever diretamente no PostgreSQL.

A plataforma externa deve apenas enviar eventos por webhook.

## 3.2. A plataforma externa informa eventos, não comandos internos

O webhook não deve permitir que a plataforma externa envie livremente qualquer status para o cartão.

Evitar contratos como:

```json
{
  "card_status": "ACTIVE"
}
```

Preferir eventos de negócio como:

```json
{
  "event_type": "subscription.payment_approved"
}
```

O backend da Soomei deve interpretar o evento recebido e decidir qual alteração interna deve ser realizada.

Exemplo:

```text
subscription.payment_approved
→ assinatura local ativa
→ cria solicitação de cartão, caso ainda não exista
→ cria cartão pendente de ativação, caso aplicável
```

Outro exemplo:

```text
subscription.overdue
→ assinatura local inadimplente
→ suspende o cartão conforme a política da Soomei
```

## 3.3. O portal deve consumir APIs, não o banco diretamente

O frontend ou portal deve continuar usando endpoints do backend Python.

Exemplos:

```http
GET /api/v1/me/cards
GET /api/v1/cards/{id}
GET /api/v1/public/cards/{slug}
PUT /api/v1/cards/{id}/profile
POST /api/v1/cards/{id}/report-lost
```

O portal não deve acessar o PostgreSQL diretamente.

---

# 4. Estrutura modular sugerida

Adaptar a estrutura à organização atual do projeto, preservando os padrões já existentes.

Exemplo:

```text
app/
├── cards/
│   ├── models.py
│   ├── schemas.py
│   ├── repository.py
│   ├── service.py
│   ├── router.py
│   └── enums.py
│
├── members/
│   ├── models.py
│   ├── schemas.py
│   ├── repository.py
│   └── service.py
│
├── subscriptions/
│   ├── models.py
│   ├── repository.py
│   ├── service.py
│   └── enums.py
│
├── integrations/
│   └── membership_platform/
│       ├── router.py
│       ├── schemas.py
│       ├── signature.py
│       ├── service.py
│       ├── repository.py
│       ├── handlers.py
│       ├── mappings.py
│       └── exceptions.py
│
├── workers/
│   └── webhook_processor.py
│
├── audit/
│   ├── models.py
│   └── service.py
│
└── shared/
    ├── config.py
    ├── database.py
    ├── security.py
    ├── logging.py
    └── exceptions.py
```

Não misturar regras de webhook diretamente nos controllers ou routers.

O router deve apenas:

* receber a requisição;
* capturar headers;
* ler o corpo bruto;
* chamar o serviço de validação;
* persistir o evento;
* retornar uma resposta HTTP.

A regra de negócio deve ficar em services e handlers.

---

# 5. Endpoint do webhook

Criar um endpoint versionado e específico para a plataforma externa.

Exemplo:

```http
POST /api/v1/webhooks/membership-platform
```

Caso existam múltiplas plataformas no futuro, manter nomes separados:

```http
POST /api/v1/webhooks/themembers
POST /api/v1/webhooks/partner-x
POST /api/v1/webhooks/partner-y
```

Não usar um endpoint genérico sem identificação do provedor.

---

# 6. Contrato esperado do webhook

O payload deve possuir, sempre que a plataforma externa suportar:

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

O código deve ser tolerante à ausência de campos não obrigatórios, mas deve rejeitar payloads sem os identificadores mínimos necessários.

Campos mínimos desejáveis:

* `event_id`;
* `event_type`;
* `created_at`;
* `customer_id`;
* `subscription_id`, quando o evento estiver relacionado à assinatura.

Nunca depender somente de e-mail como identificador.

Usar preferencialmente:

* `provider`;
* `external_customer_id`;
* `external_subscription_id`;
* `external_order_id`;
* `external_event_id`.

---

# 7. Eventos de negócio suportados

Criar uma camada de mapeamento entre os nomes enviados pela plataforma externa e os eventos internos da Soomei.

Eventos internos sugeridos:

```text
CUSTOMER_CREATED
SUBSCRIPTION_CREATED
PAYMENT_APPROVED
PAYMENT_FAILED
SUBSCRIPTION_OVERDUE
SUBSCRIPTION_REACTIVATED
SUBSCRIPTION_CANCELLED
PAYMENT_REFUNDED
CHARGEBACK_RECEIVED
```

Não espalhar strings de eventos pelo código.

Criar enum ou constantes centralizadas.

Exemplo:

```python
from enum import StrEnum

class ExternalEventType(StrEnum):
    CUSTOMER_CREATED = "customer.created"
    SUBSCRIPTION_CREATED = "subscription.created"
    PAYMENT_APPROVED = "subscription.payment_approved"
    PAYMENT_FAILED = "subscription.payment_failed"
    SUBSCRIPTION_OVERDUE = "subscription.overdue"
    SUBSCRIPTION_REACTIVATED = "subscription.reactivated"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    PAYMENT_REFUNDED = "payment.refunded"
    CHARGEBACK_RECEIVED = "payment.chargeback"
```

Eventos desconhecidos não devem quebrar o endpoint.

Eles devem ser:

* persistidos;
* marcados como `IGNORED` ou `UNSUPPORTED`;
* registrados em log;
* respondidos com sucesso caso o webhook seja válido.

Isso evita retentativas infinitas da plataforma externa para eventos que a Soomei ainda não processa.

---

# 8. Segurança obrigatória

## 8.1. HTTPS obrigatório

O endpoint deve funcionar exclusivamente por HTTPS em produção.

Nunca aceitar comunicação HTTP em produção.

Configurar redirecionamento ou rejeição de tráfego não seguro no proxy reverso, gateway ou load balancer.

## 8.2. Assinatura HMAC-SHA256

A forma preferencial de autenticação do webhook é assinatura HMAC-SHA256.

A plataforma externa e a Soomei compartilharão um segredo.

Exemplo:

```text
SOOMEI_MEMBERSHIP_WEBHOOK_SECRET
```

A plataforma externa deverá calcular:

```text
HMAC-SHA256(secret, timestamp + "." + raw_body)
```

Headers sugeridos:

```http
X-Webhook-Timestamp: 1783809000
X-Webhook-Signature: sha256=abcdef123456...
X-Webhook-Event-Id: evt_987654321
```

O backend deve:

1. ler o corpo bruto da requisição;
2. obter o timestamp;
3. validar se o timestamp é numérico;
4. validar se o timestamp está dentro da janela permitida;
5. reconstruir o conteúdo assinado;
6. calcular o HMAC localmente;
7. comparar a assinatura com comparação constante;
8. rejeitar a requisição se a assinatura for inválida.

Nunca validar a assinatura depois de serializar novamente o JSON.

A validação deve usar exatamente os bytes recebidos.

Exemplo:

```python
raw_body = await request.body()
```

## 8.3. Proteção contra replay attack

Aceitar somente timestamps recentes.

Janela sugerida:

```text
5 minutos
```

Rejeitar requisições cujo timestamp esteja fora da janela.

Além disso, usar o `event_id` para impedir o reprocessamento do mesmo evento.

## 8.4. Comparação constante

Usar:

```python
hmac.compare_digest()
```

Não usar comparação simples com `==`.

Isso reduz risco de timing attack.

## 8.5. Segredos

O segredo do webhook:

* não deve ficar hardcoded;
* não deve ser commitado no Git;
* não deve aparecer em logs;
* não deve ser enviado ao frontend;
* não deve ficar em arquivos públicos;
* deve ser diferente entre produção e homologação.

Carregar por variável de ambiente ou secret manager.

Exemplos:

* AWS Secrets Manager;
* Azure Key Vault;
* Google Secret Manager;
* Docker Secret;
* Kubernetes Secret;
* variável protegida no ambiente de deploy.

## 8.6. Rotação de segredo

Preparar a aplicação para aceitar temporariamente:

```text
current_secret
previous_secret
```

Isso permite trocar o segredo sem interromper a integração.

Durante a validação:

1. tentar validar com o segredo atual;
2. caso falhe, tentar com o segredo anterior;
3. remover o segredo anterior após concluir a rotação.

## 8.7. Rate limiting

Aplicar limite de requisições no endpoint.

Exemplo inicial:

```text
100 requisições por minuto por IP
```

O limite deve ser configurável.

O rate limit pode ficar no:

* Nginx;
* API Gateway;
* Cloudflare;
* Traefik;
* middleware da aplicação.

## 8.8. Allowlist de IP

Caso a plataforma externa forneça IPs fixos, permitir uma lista de IPs autorizados.

A allowlist deve ser apenas uma camada adicional.

Não depender somente dela.

O HMAC continua obrigatório sempre que a plataforma suportar.

## 8.9. Validação rígida do payload

Usar schemas de validação, preferencialmente Pydantic.

Validar:

* tamanho máximo do payload;
* tipos de dados;
* formato de datas;
* formato de IDs;
* campos obrigatórios;
* limites de tamanho em strings;
* valores de enums.

Rejeitar payloads malformados.

Não executar comandos com base em campos livres sem validação.

## 8.10. Proteção de dados pessoais

Não registrar nos logs:

* CPF completo;
* CNPJ completo;
* número de cartão;
* dados bancários;
* tokens;
* segredos;
* senhas;
* conteúdo integral do payload em logs comuns.

O payload integral pode ser persistido na tabela de webhook para auditoria, desde que:

* o banco esteja protegido;
* o acesso seja restrito;
* exista política de retenção;
* campos sensíveis sejam tratados adequadamente.

---

# 9. Alternativa caso a plataforma externa não suporte HMAC

Verificar a documentação real da plataforma antes de implementar.

Ordem de preferência:

1. HMAC-SHA256;
2. assinatura assimétrica;
3. OAuth2 Client Credentials;
4. Bearer Token em header;
5. URL secreta com token;
6. allowlist de IP como complemento.

Caso a plataforma suporte apenas Bearer Token:

```http
Authorization: Bearer <secret>
```

Implementar:

* token longo e aleatório;
* comparação constante;
* rotação;
* HTTPS;
* rate limit;
* allowlist de IP, se possível;
* idempotência;
* auditoria.

Caso a plataforma apenas permita configurar uma URL:

```text
https://api.soomei.com.br/api/v1/webhooks/membership-platform/<token-secreto>
```

Esse token deve:

* possuir alta entropia;
* ser revogável;
* ser diferente por ambiente;
* nunca aparecer em logs;
* nunca ser exposto no frontend.

Essa abordagem deve ser usada apenas como último recurso.

---

# 10. Idempotência obrigatória

Webhooks podem ser enviados mais de uma vez.

A plataforma externa pode reenviar um evento quando:

* ocorre timeout;
* a resposta não chega;
* a conexão é interrompida;
* o provedor executa retentativas;
* existe reenvio manual;
* ocorre falha temporária.

Cada evento deve possuir um identificador único.

Criar uma tabela semelhante a:

```sql
CREATE TABLE webhook_events (
    id UUID PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    external_event_id VARCHAR(150) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(30) NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_started_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    error_code VARCHAR(100),
    error_message TEXT,

    CONSTRAINT uk_webhook_event
        UNIQUE (provider, external_event_id)
);
```

Status sugeridos:

```text
RECEIVED
PROCESSING
PROCESSED
FAILED
RETRY_PENDING
IGNORED
DEAD_LETTER
```

Quando o mesmo evento chegar novamente:

* não criar outro cartão;
* não aplicar novamente a operação;
* não retornar erro desnecessário;
* retornar HTTP 200 ou 202;
* registrar que o evento já havia sido recebido.

Além da tabela de eventos, criar restrições de unicidade no domínio.

Exemplo:

```sql
UNIQUE(provider, external_subscription_id)
```

E, caso cada assinatura gere somente um cartão:

```sql
UNIQUE(provider, external_subscription_id, product_id)
```

A idempotência deve existir em duas camadas:

1. evento externo único;
2. entidade de negócio única.

---

# 11. Persistência das referências externas

Criar uma entidade para representar o vínculo entre o associado local e a assinatura externa.

Exemplo:

```sql
CREATE TABLE external_subscriptions (
    id UUID PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,

    external_customer_id VARCHAR(150) NOT NULL,
    external_subscription_id VARCHAR(150),
    external_order_id VARCHAR(150),
    external_product_id VARCHAR(150),
    external_plan_id VARCHAR(150),

    member_id UUID REFERENCES members(id),

    status VARCHAR(30) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (provider, external_customer_id),
    UNIQUE (provider, external_subscription_id)
);
```

Não usar e-mail como chave principal de integração.

O e-mail pode ser utilizado apenas como informação auxiliar ou estratégia secundária de conciliação.

---

# 12. Separação de status

Não usar um único status para representar associação, cobrança, produção e cartão.

## 12.1. Status da assinatura

Exemplo:

```python
class SubscriptionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    OVERDUE = "OVERDUE"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
```

## 12.2. Status operacional do cartão

Exemplo:

```python
class CardStatus(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    PENDING_PRODUCTION = "PENDING_PRODUCTION"
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    LOST = "LOST"
    STOLEN = "STOLEN"
    REPLACED = "REPLACED"
    CANCELLED = "CANCELLED"
```

## 12.3. Motivo do status

Exemplo:

```python
class CardStatusReason(StrEnum):
    PAYMENT_APPROVED = "PAYMENT_APPROVED"
    PAYMENT_OVERDUE = "PAYMENT_OVERDUE"
    PAYMENT_REGULARIZED = "PAYMENT_REGULARIZED"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED"
    REPORTED_LOST = "REPORTED_LOST"
    REPORTED_STOLEN = "REPORTED_STOLEN"
    CARD_REPLACED = "CARD_REPLACED"
    ADMIN_ACTION = "ADMIN_ACTION"
```

Um cartão suspenso por inadimplência pode ser reativado automaticamente.

Um cartão perdido, roubado, substituído ou cancelado não deve ser reativado automaticamente apenas porque houve regularização de pagamento.

---

# 13. Regras de negócio

## 13.1. Pagamento aprovado

Quando receber:

```text
PAYMENT_APPROVED
```

Executar:

1. localizar assinatura externa;
2. criar assinatura local caso não exista;
3. localizar associado pelo identificador externo;
4. criar associado local, caso não exista;
5. atualizar dados básicos do associado;
6. marcar assinatura como ativa;
7. verificar se já existe cartão relacionado à assinatura ou produto;
8. criar o cartão somente se ainda não existir;
9. registrar histórico;
10. marcar webhook como processado.

O cartão poderá nascer como:

```text
PENDING_PRODUCTION
```

ou:

```text
PENDING_ACTIVATION
```

A escolha deve respeitar o fluxo físico real da Soomei.

Não criar diretamente como `ACTIVE` se ainda houver etapa de produção, entrega ou ativação.

## 13.2. Inadimplência

Quando receber:

```text
SUBSCRIPTION_OVERDUE
```

Executar:

1. localizar assinatura;
2. marcar assinatura como `OVERDUE`;
3. aplicar política de carência, caso exista;
4. suspender o cartão após a carência;
5. usar motivo `PAYMENT_OVERDUE`;
6. registrar histórico.

Não cancelar automaticamente o cartão na primeira falha de cobrança, a menos que essa seja uma regra expressa da Soomei.

## 13.3. Regularização

Quando receber:

```text
SUBSCRIPTION_REACTIVATED
```

Executar:

1. localizar assinatura;
2. marcar assinatura como `ACTIVE`;
3. localizar cartão;
4. reativar somente se:

   * o status atual for `SUSPENDED`;
   * o motivo da suspensão for `PAYMENT_OVERDUE`.

Não reativar automaticamente cartões:

* perdidos;
* roubados;
* substituídos;
* cancelados;
* bloqueados manualmente por fraude ou suporte.

## 13.4. Cancelamento

Quando receber:

```text
SUBSCRIPTION_CANCELLED
```

Executar:

1. marcar assinatura como `CANCELLED`;
2. aplicar a política de cancelamento definida;
3. suspender ou cancelar o cartão;
4. registrar o motivo;
5. preservar o histórico.

Não excluir fisicamente o cartão ou associado.

Usar exclusão lógica ou status.

## 13.5. Estorno ou chargeback

Quando receber:

```text
PAYMENT_REFUNDED
```

ou:

```text
CHARGEBACK_RECEIVED
```

Executar de acordo com política explícita.

Sugestão:

* marcar assinatura como `REFUNDED` ou `SUSPENDED`;
* suspender cartão;
* registrar histórico;
* gerar alerta administrativo.

---

# 14. Histórico de status

Criar uma tabela de histórico.

Exemplo:

```sql
CREATE TABLE card_status_history (
    id UUID PRIMARY KEY,
    card_id UUID NOT NULL REFERENCES cards(id),

    previous_status VARCHAR(40),
    new_status VARCHAR(40) NOT NULL,
    reason VARCHAR(60) NOT NULL,

    source VARCHAR(40) NOT NULL,
    actor_id VARCHAR(150),
    external_event_id VARCHAR(150),

    metadata JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Valores de `source`:

```text
WEBHOOK
ADMIN_PANEL
MEMBER_PORTAL
SYSTEM
SUPPORT
```

Toda mudança de status deve gerar histórico.

Nunca atualizar apenas o status atual sem registrar a transição.

---

# 15. Processamento assíncrono

O endpoint não deve executar tarefas demoradas.

Fluxo recomendado:

```text
1. receber webhook
2. validar segurança
3. validar formato mínimo
4. persistir o evento
5. responder rapidamente
6. processar o evento em worker
```

Resposta sugerida:

```http
202 Accepted
```

ou:

```http
200 OK
```

De acordo com o comportamento esperado pela plataforma externa.

O processamento assíncrono pode inicialmente usar o próprio PostgreSQL.

Exemplo:

```text
webhook_events.status = RECEIVED
```

Um worker busca eventos pendentes:

```text
RECEIVED
→ PROCESSING
→ PROCESSED
```

Para concorrência segura, considerar:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
```

Isso permite múltiplos workers sem processar o mesmo evento simultaneamente.

No MVP, não é obrigatório introduzir RabbitMQ, Kafka ou Redis.

Criar a abstração de processamento de forma que uma fila possa ser adicionada no futuro.

---

# 16. Política de retry

Falhas temporárias devem gerar nova tentativa.

Exemplos de falhas temporárias:

* conexão com banco indisponível;
* timeout;
* indisponibilidade de serviço interno;
* conflito de lock;
* falha transitória de infraestrutura.

Implementar retry com backoff.

Exemplo:

```text
1ª tentativa: imediata
2ª tentativa: 1 minuto
3ª tentativa: 5 minutos
4ª tentativa: 15 minutos
5ª tentativa: 1 hora
```

Após o limite, marcar como:

```text
DEAD_LETTER
```

Falhas permanentes não devem ser repetidas indefinidamente.

Exemplos:

* payload inválido;
* assinatura inválida;
* campo obrigatório ausente;
* tipo de evento impossível;
* identificador externo inconsistente.

---

# 17. Concorrência e transações

Toda operação de criação ou alteração deve acontecer dentro de transação de banco.

Exemplo:

```text
processar webhook
→ localizar assinatura
→ criar ou atualizar associado
→ criar ou atualizar cartão
→ registrar histórico
→ marcar evento como processado
```

Essas operações devem ser atômicas sempre que possível.

Caso uma etapa falhe, evitar estado parcialmente persistido.

Adicionar controle de concorrência por:

* constraints de unicidade;
* locks;
* optimistic locking, caso a ORM suporte;
* tratamento de `IntegrityError`.

Não confiar apenas em verificações do tipo:

```python
if not exists:
    create()
```

Duas requisições concorrentes podem passar pela verificação ao mesmo tempo.

O banco deve garantir unicidade.

---

# 18. Logs e auditoria

Usar logs estruturados.

Campos recomendados:

```text
provider
external_event_id
event_type
external_customer_id
external_subscription_id
webhook_status
processing_status
duration_ms
error_code
correlation_id
```

Não registrar segredo ou assinatura completa.

Gerar ou propagar um `correlation_id`.

Exemplo:

```http
X-Correlation-Id
```

Caso o header não exista, gerar um UUID.

Todo log relacionado ao evento deve conter o mesmo correlation ID.

---

# 19. Respostas HTTP

## 19.1. Requisição válida e aceita

```http
200 OK
```

ou:

```http
202 Accepted
```

Exemplo:

```json
{
  "received": true,
  "event_id": "evt_987654321"
}
```

## 19.2. Evento duplicado

Retornar sucesso.

Exemplo:

```http
200 OK
```

```json
{
  "received": true,
  "duplicate": true
}
```

Não retornar erro para evento já processado.

## 19.3. Assinatura inválida

```http
401 Unauthorized
```

ou:

```http
403 Forbidden
```

Não revelar detalhes da validação.

Resposta:

```json
{
  "detail": "Invalid webhook authentication"
}
```

## 19.4. Payload inválido

```http
422 Unprocessable Entity
```

ou:

```http
400 Bad Request
```

## 19.5. Rate limit

```http
429 Too Many Requests
```

---

# 20. Exemplo de validação HMAC em Python

Adaptar ao framework atual.

```python
import hashlib
import hmac
import time

from fastapi import HTTPException, status

MAX_WEBHOOK_DELAY_SECONDS = 300


def validate_webhook_signature(
    *,
    secret: str,
    timestamp: str,
    received_signature: str,
    raw_body: bytes,
) -> None:
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook authentication",
        ) from exc

    current_timestamp = int(time.time())

    if abs(current_timestamp - timestamp_value) > MAX_WEBHOOK_DELAY_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook authentication",
        )

    signed_payload = (
        timestamp.encode("utf-8")
        + b"."
        + raw_body
    )

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    normalized_received_signature = (
        received_signature
        .removeprefix("sha256=")
        .strip()
    )

    if not hmac.compare_digest(
        expected_signature,
        normalized_received_signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook authentication",
        )
```

---

# 21. Exemplo de endpoint FastAPI

Adaptar ao framework real do projeto.

```python
from fastapi import APIRouter, Header, Request, status

router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["webhooks"],
)


@router.post(
    "/membership-platform",
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_membership_webhook(
    request: Request,
    x_webhook_timestamp: str = Header(...),
    x_webhook_signature: str = Header(...),
    x_webhook_event_id: str = Header(...),
):
    raw_body = await request.body()

    validate_webhook_signature(
        secret=settings.membership_webhook_secret,
        timestamp=x_webhook_timestamp,
        received_signature=x_webhook_signature,
        raw_body=raw_body,
    )

    event = await webhook_service.register_event(
        provider="membership_platform",
        external_event_id=x_webhook_event_id,
        raw_body=raw_body,
    )

    return {
        "received": True,
        "event_id": event.external_event_id,
        "duplicate": event.was_duplicate,
    }
```

O código acima é apenas referência.

Antes de implementar, inspecionar:

* framework atual;
* ORM atual;
* padrão de repositories;
* padrão de migrations;
* padrão de configuração;
* padrão de testes;
* padrão de exceções;
* estilo do projeto.

Não introduzir nova stack sem necessidade.

---

# 22. Migrations

Usar a ferramenta de migrations já existente.

Caso o projeto utilize SQLAlchemy, preferir Alembic.

Criar migrations para:

* `webhook_events`;
* `external_subscriptions`;
* novos campos em `cards`;
* `card_status_history`;
* índices;
* constraints de unicidade;
* índices por status;
* índices por data de recebimento.

Índices recomendados:

```sql
CREATE INDEX idx_webhook_events_status_received_at
ON webhook_events(status, received_at);

CREATE INDEX idx_webhook_events_retry
ON webhook_events(status, next_retry_at);

CREATE INDEX idx_external_subscriptions_customer
ON external_subscriptions(provider, external_customer_id);

CREATE INDEX idx_external_subscriptions_subscription
ON external_subscriptions(provider, external_subscription_id);

CREATE INDEX idx_card_status_history_card_created
ON card_status_history(card_id, created_at DESC);
```

---

# 23. Testes obrigatórios

Implementar testes unitários e de integração.

## 23.1. Segurança

Testar:

* assinatura válida;
* assinatura inválida;
* timestamp expirado;
* timestamp futuro fora da tolerância;
* header ausente;
* segredo incorreto;
* payload alterado após assinatura;
* rotação de segredo;
* comparação com segredo anterior.

## 23.2. Idempotência

Testar:

* mesmo evento enviado duas vezes;
* dois eventos concorrentes com o mesmo ID;
* dois eventos diferentes para a mesma assinatura;
* tentativa de criar dois cartões para a mesma assinatura;
* constraint de unicidade.

## 23.3. Regras de negócio

Testar:

* pagamento aprovado cria associado;
* pagamento aprovado cria assinatura;
* pagamento aprovado cria cartão quando não existe;
* pagamento aprovado não duplica cartão;
* inadimplência suspende;
* regularização reativa apenas suspensão por inadimplência;
* regularização não reativa cartão perdido;
* regularização não reativa cartão roubado;
* cancelamento atualiza assinatura e cartão;
* evento desconhecido é ignorado com segurança;
* payload inválido não é processado.

## 23.4. Processamento assíncrono

Testar:

* evento passa de `RECEIVED` para `PROCESSING`;
* evento passa de `PROCESSING` para `PROCESSED`;
* falha temporária gera retry;
* falha definitiva gera `FAILED` ou `DEAD_LETTER`;
* múltiplos workers não processam o mesmo evento.

## 23.5. Auditoria

Testar:

* toda mudança de status gera histórico;
* `external_event_id` é associado ao histórico;
* status anterior e novo são gravados;
* motivo correto é registrado.

---

# 24. Observabilidade

Adicionar métricas, caso o projeto já tenha suporte.

Métricas sugeridas:

```text
webhooks_received_total
webhooks_processed_total
webhooks_failed_total
webhooks_duplicate_total
webhooks_invalid_signature_total
webhook_processing_duration_seconds
webhook_queue_size
webhook_dead_letter_total
cards_created_from_webhook_total
cards_suspended_from_webhook_total
cards_reactivated_from_webhook_total
```

Criar alertas para:

* aumento de assinaturas inválidas;
* aumento de falhas;
* eventos presos em `PROCESSING`;
* crescimento da fila;
* eventos em `DEAD_LETTER`;
* ausência completa de webhooks por período inesperado.

---

# 25. LGPD e retenção

Implementar tratamento compatível com LGPD.

Requisitos:

* armazenar apenas dados necessários;
* restringir acesso aos payloads;
* não expor dados pessoais no endpoint público;
* não informar publicamente que o associado está inadimplente;
* aplicar política de retenção aos eventos;
* permitir anonimização ou exclusão quando juridicamente aplicável;
* preservar dados exigidos por auditoria ou obrigação legal.

Na página pública do cartão, em caso de suspensão, mostrar mensagem genérica.

Exemplo:

```json
{
  "availability": "TEMPORARILY_UNAVAILABLE",
  "message": "Este cartão está temporariamente indisponível."
}
```

Não retornar:

```text
Cliente inadimplente
Pagamento atrasado
Assinatura não paga
```

---

# 26. Configurações de ambiente

Adicionar configurações semelhantes a:

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

Validar configurações no startup.

A aplicação deve falhar ao iniciar em produção caso:

* integração esteja habilitada;
* segredo obrigatório não esteja configurado;
* configuração esteja inconsistente.

---

# 27. Documentação técnica

Criar documentação contendo:

* URL do webhook;
* método HTTP;
* headers obrigatórios;
* algoritmo de assinatura;
* exemplo de payload;
* códigos de resposta;
* eventos suportados;
* política de retry;
* política de idempotência;
* processo de rotação de segredo;
* procedimento de homologação;
* procedimento de reprocessamento;
* consulta de eventos falhos.

Não publicar o segredo na documentação.

---

# 28. Ferramentas administrativas

Criar, se já existir backoffice, uma tela ou endpoint administrativo para:

* listar webhooks recebidos;
* filtrar por status;
* filtrar por `external_event_id`;
* filtrar por assinatura;
* visualizar erro;
* reprocessar evento falho;
* mover evento para `DEAD_LETTER`;
* visualizar histórico de status;
* verificar se um evento criou ou alterou um cartão.

Esses endpoints devem exigir autenticação administrativa.

Nunca expor operações administrativas no endpoint público de webhook.

---

# 29. Reprocessamento manual

Criar uma operação segura.

Exemplo:

```http
POST /api/v1/admin/webhook-events/{id}/retry
```

Regras:

* somente usuários autorizados;
* registrar quem solicitou;
* registrar data e hora;
* não alterar o `external_event_id`;
* preservar o payload original;
* impedir reprocessamento simultâneo;
* respeitar idempotência.

---

# 30. Critérios de aceite

A implementação será considerada concluída quando:

1. o endpoint receber webhooks da plataforma externa;
2. a autenticação do webhook estiver implementada;
3. o corpo bruto for usado para validar a assinatura;
4. requisições inválidas forem rejeitadas;
5. eventos duplicados não gerarem cartões duplicados;
6. o evento for persistido antes do processamento;
7. o processamento ocorrer de maneira resiliente;
8. o cartão for criado após evento válido de pagamento;
9. inadimplência puder suspender o cartão;
10. regularização reativar somente cartões suspensos por pagamento;
11. cartões perdidos ou roubados não forem reativados;
12. toda mudança gerar histórico;
13. segredos não aparecerem no código ou logs;
14. o portal continuar consumindo as APIs existentes;
15. o frontend não precisar conhecer a plataforma externa;
16. migrations estiverem criadas;
17. testes estiverem implementados;
18. documentação estiver atualizada;
19. houver tratamento de retry;
20. houver suporte a idempotência por evento e por assinatura.

---

# 31. Sequência de execução para o Codex

Antes de alterar qualquer código:

1. inspecione a estrutura atual do backend Python;
2. identifique o framework utilizado;
3. identifique a ORM;
4. identifique o sistema de migrations;
5. identifique o padrão de repositories e services;
6. identifique as tabelas atuais de associado e cartão;
7. identifique os enums e status existentes;
8. identifique como o portal consulta os cartões;
9. identifique a configuração de segurança;
10. identifique o padrão de testes.

Depois:

1. proponha uma lista objetiva de arquivos a criar ou alterar;
2. preserve a arquitetura atual sempre que estiver adequada;
3. não reescreva partes não relacionadas;
4. crie migrations reversíveis;
5. implemente o receptor de webhook;
6. implemente validação de segurança;
7. implemente persistência idempotente;
8. implemente handlers de eventos;
9. implemente histórico;
10. implemente worker;
11. implemente testes;
12. atualize documentação;
13. execute os testes;
14. execute lint e type checking;
15. apresente um resumo das alterações.

---

# 32. Restrições para a implementação

Não fazer:

* não criar microsserviço Java;
* não criar um banco separado sem necessidade;
* não permitir acesso direto ao banco pela plataforma externa;
* não usar e-mail como único identificador;
* não criar cartão diretamente no controller;
* não colocar regra de negócio no router;
* não ignorar idempotência;
* não armazenar segredo no repositório;
* não registrar segredo em log;
* não aceitar qualquer status vindo da plataforma externa;
* não reativar cartão perdido ou roubado;
* não excluir histórico;
* não criar infraestrutura complexa sem necessidade;
* não adicionar RabbitMQ ou Kafka no MVP sem justificativa;
* não alterar APIs existentes sem verificar impacto;
* não quebrar compatibilidade com o portal atual.

---

# 33. Resultado arquitetural esperado

```text
┌──────────────────────────────────┐
│ Plataforma externa de associados │
│ Cadastro, pagamento e assinatura │
└────────────────┬─────────────────┘
                 │
                 │ Webhook HTTPS assinado
                 ▼
┌──────────────────────────────────┐
│ Backend Python Soomei Cards       │
│                                  │
│ Webhook Router                   │
│ Security Validator               │
│ Webhook Inbox                    │
│ Event Processor                  │
│ Member Service                   │
│ Subscription Service             │
│ Card Service                     │
│ Audit Service                    │
└────────────────┬─────────────────┘
                 │
                 ▼
┌──────────────────────────────────┐
│ PostgreSQL                        │
│                                  │
│ members                          │
│ external_subscriptions           │
│ cards                            │
│ card_status_history              │
│ webhook_events                   │
└────────────────┬─────────────────┘
                 │
                 │ APIs do backend
                 ▼
┌──────────────────────────────────┐
│ Portal de cartões NFC             │
│                                  │
│ Consulta e apresenta o estado    │
│ sem conhecer a plataforma externa│
└──────────────────────────────────┘
```

---

# 34. Decisão técnica final

Implementar a integração diretamente no backend Python atual do portal de cartões.

Esse backend deve ser o único proprietário das regras e tabelas relacionadas aos cartões.

A plataforma externa deve apenas notificar eventos por webhook.

O PostgreSQL deve armazenar:

* estado atual;
* referências externas;
* eventos recebidos;
* histórico;
* erros;
* tentativas;
* auditoria.

O portal deve continuar operando de forma transparente, consultando as APIs do backend Python e apresentando o estado atual dos cartões.

Priorizar:

```text
segurança
idempotência
consistência
auditoria
simplicidade operacional
testabilidade
evolução futura
```
