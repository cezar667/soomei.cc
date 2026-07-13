# Indicações e recompensas

O módulo permite que um associado compartilhe um código de indicação e que um novo associado informe esse código ao ativar o cartão.

## Fluxo

1. O dono do cartão acessa `/edit/{slug}`.
2. A seção **Conecte e ganhe** exibe o código de indicação.
3. O usuário copia ou compartilha o convite.
4. O novo associado ativa o cartão em `/onboard/{uid}` e preenche o campo opcional **Código de indicação**.
5. Se o código for válido, a indicação nasce como `pending_validation`.
6. Webhooks de perda de acesso, cancelamento, reembolso, inadimplência ou chargeback desqualificam imediatamente a indicação pendente do indicado.
7. Após a janela de validação (`REFERRAL_QUALIFICATION_DAYS`, padrão: 30 dias), a rotina diária qualifica as indicações que continuaram em `pending_validation`.
8. Se a indicação qualificar:
   - indicador recebe +30 dias de **Destaque Soomei**;
   - indicador recebe 1 cupom da campanha **Pix da Virada**;
   - indicado recebe 1 cupom da campanha **Pix da Virada** como bônus de participação/boas-vindas.
9. Se antes disso chegar um webhook negativo do indicado, a indicação é marcada como `disqualified` e nenhum benefício é concedido.

O **Destaque Soomei** é liberado apenas para quem indica. Ele é exibido como um selo premium sobre a foto do perfil público. A intenção de UX é transmitir maior visibilidade, prestígio e autoridade comercial para clientes em potencial, sem sugerir validação jurídica de identidade ou certificação profissional.

Código inválido não bloqueia a ativação do cartão.

Na tela de edição, o associado vê:

- indicações em validação;
- data da próxima validação prevista;
- indicações qualificadas;
- dias restantes de Destaque Soomei;
- cupons ativos da campanha Pix da Virada.

## Banco de dados

Migration:

```bash
alembic upgrade head
```

Tabelas criadas:

- `referral_codes`
- `referrals`
- `profile_badges`
- `referral_campaigns`
- `referral_rewards`
- `raffle_entries`

A migration `20260713_0004_referral_qualification_delay` adiciona:

- `referrals.qualify_after`;
- índice `idx_referrals_pending_qualification` em `(status, qualify_after)`.

## Admin

Tela:

```text
/referrals
```

Ela mostra:

- total de indicações;
- indicações em validação;
- indicações qualificadas;
- indicações desqualificadas via filtro;
- selos ativos;
- cupons ativos;
- listagem com indicador, indicado, código e status.

## Rotina diária de qualificação

O script abaixo processa apenas indicações vencidas que ainda estão em `pending_validation`. Ele não consulta a TheMembers; os cancelamentos e perdas de acesso são refletidos pelos webhooks. Uma indicação já qualificada/desqualificada não é processada de novo.

```bash
python scripts/process_referral_qualifications.py --limit 500
```

Saída esperada:

```text
referral_qualifications processed=3 qualified=2 disqualified=1
```

## Configuração em produção

Variáveis recomendadas nos serviços `soomei.service` e `soomei-admin.service`:

```ini
Environment=REFERRAL_QUALIFICATION_DAYS=30
Environment=REFERRAL_QUALIFICATION_BATCH_SIZE=500
```

Depois de alterar os arquivos do systemd:

```bash
sudo systemctl daemon-reload
sudo systemctl restart soomei.service
sudo systemctl restart soomei-admin.service
```

Também rode as migrations antes de reiniciar ou imediatamente após publicar o código:

```bash
cd /opt/soomei/app
source .venv/bin/activate
export DATABASE_URL='postgresql+psycopg://USUARIO:SENHA@localhost:5432/soomei'
alembic upgrade head
```

> Em produção, prefira carregar `DATABASE_URL` por arquivo de ambiente protegido em vez de digitar/commitar senha em documentação, shell history ou repositório.

### systemd timer recomendado

Crie `/etc/systemd/system/soomei-referrals.service`:

```ini
[Unit]
Description=Soomei referral qualification job

[Service]
Type=oneshot
WorkingDirectory=/opt/soomei/app
Environment=APP_ENV=prod
Environment=DATABASE_URL=postgresql+psycopg://USUARIO:SENHA@localhost:5432/soomei
Environment=REFERRAL_QUALIFICATION_DAYS=30
Environment=REFERRAL_QUALIFICATION_BATCH_SIZE=500
ExecStart=/opt/soomei/app/.venv/bin/python scripts/process_referral_qualifications.py --limit 500
```

Crie `/etc/systemd/system/soomei-referrals.timer`:

```ini
[Unit]
Description=Run Soomei referral qualification daily

[Timer]
OnCalendar=*-*-* 03:10:00
Persistent=true
Unit=soomei-referrals.service

[Install]
WantedBy=timers.target
```

Ative:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now soomei-referrals.timer
sudo systemctl status soomei-referrals.timer --no-pager
```

Executar manualmente quando precisar:

```bash
sudo systemctl start soomei-referrals.service
sudo journalctl -u soomei-referrals.service -n 50 --no-pager
```

## Testes

```bash
python -m pytest tests/test_referrals.py
```

Ou suíte completa:

```bash
python -m pytest
```

## Observação jurídica

O sistema registra cupons da campanha Pix da Virada, mas não executa sorteio. Regulamento, elegibilidade, apuração e premiação devem ser definidos e revisados juridicamente antes de qualquer campanha pública com prêmio financeiro.
