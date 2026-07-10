# Soomei — Cartão NFC + Cartão Digital

Aplicação de cartão de visita digital com QR Code, vCard e roteamento NFC.

- API pública em FastAPI, com HTML renderizado no servidor
- painel administrativo em uma aplicação FastAPI separada
- Postgres como banco principal, acessado com SQLAlchemy
- Cloudflare Worker para a rota curta `/r/{uid}`
- scripts para cadastrar, resetar e gravar cartões NFC

## Estrutura do projeto

- `api/` — API pública, admin, domínio, serviços e acesso ao Postgres
- `cloudflare/` — Worker que consulta o KV `CARDS` e encaminha o visitante
- `scripts/` — cadastro/reset de cartões, migração legada e gravação NFC
- `templates/` — templates HTML
- `web/` — CSS, imagens e uploads locais
- `tests/` — testes do repositório SQL e autenticação
- `api/data.json` — somente fonte para importação legada; não é banco de runtime

## Instalação completa no macOS

Os comandos abaixo partem de um Mac com [Homebrew](https://brew.sh/) e do terminal aberto na raiz deste repositório.

### 1. Instalar as ferramentas

```bash
xcode-select --install
brew update
brew install python@3.11 postgresql@18 node
```

Se o Homebrew informar que o PostgreSQL não está no `PATH`, adicione-o ao Zsh:

```bash
echo 'export PATH="$(brew --prefix postgresql@18)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Confira as instalações:

```bash
python3.11 --version
psql --version
node --version
npm --version
```

### 2. Criar e ativar o ambiente Python

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r api/requirements.txt
python -m pip install pytest
```

Se a criação da `.venv` for interrompida com `Ctrl+C`, aparecerão `^C`, `KeyboardInterrupt` e uma referência ao `ensurepip`. Isso significa que o processo foi cancelado enquanto instalava o `pip`, não necessariamente que o Python apresentou uma falha. Renomeie o ambiente incompleto e crie outro, aguardando o prompt reaparecer:

```bash
mv .venv .venv-incompleta
python3.11 -m venv .venv
source .venv/bin/activate
python --version
python -m pip --version
```

Se o `ensurepip` continuar parado por vários minutos, verifique e repare o Python:

```bash
python3.11 -m ensurepip --version
brew reinstall python@3.11
```

Nas próximas sessões, basta entrar na pasta do projeto e reativar o ambiente:

```bash
cd /caminho/para/soomei.cc
source .venv/bin/activate
```

Para sair do ambiente virtual:

```bash
deactivate
```

### 3. Iniciar e preparar o Postgres

Inicie o serviço e crie o banco local:

```bash
brew services start postgresql@18
createdb soomei
```

Se o banco já existir, o erro de `createdb` pode ser ignorado. Teste a conexão:

```bash
psql -d soomei -c 'SELECT current_database();'
```

### PostgreSQL instalado pelo navegador e pelo Homebrew

O instalador baixado pela internet (por exemplo, EDB ou Postgres.app) e o pacote instalado pelo Homebrew são instalações distintas. Cada uma pode possuir executáveis, serviço, diretório de dados, usuários, senhas e até portas diferentes. Evite manter dois servidores disputando a porta `5432`.

Descubra qual cliente o terminal está usando:

```bash
which psql
which createdb
psql --version
createdb --version
brew services list
lsof -nP -iTCP:5432 -sTCP:LISTEN
```

Caminhos comuns:

- `/opt/homebrew/...` — Homebrew em Mac Apple Silicon
- `/usr/local/...` — normalmente Homebrew em Mac Intel
- `/Library/PostgreSQL/18/...` — instalador da EDB
- `/Applications/Postgres.app/...` — Postgres.app

Se uma conexão já funciona no VS Code, consulte nela o host, porta e usuário e reutilize exatamente esses dados no terminal. Para uma instalação que usa o administrador `postgres`:

```bash
createdb -h localhost -p 5432 -U postgres soomei
```

A senha solicitada é a senha definida para o usuário `postgres` durante a instalação; não existe uma senha padrão. Também é possível criar o banco pela conexão administrativa do VS Code:

```sql
CREATE DATABASE soomei;
```

Escolha uma única instalação para executar o servidor. Se optar pela instalação baixada pelo navegador, pare apenas o serviço duplicado do Homebrew:

```bash
brew services stop postgresql@18
```

Não desinstale nem apague diretórios de dados antes de confirmar em qual instância estão seus bancos.

Defina as variáveis usadas pela aplicação:

```bash
export APP_ENV=dev
export DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/soomei"
export PUBLIC_BASE_URL="http://localhost:8000"
```

Se estiver usando o usuário `postgres` com senha, a URL será semelhante a:

```bash
export DATABASE_URL="postgresql+psycopg://postgres:SUA_SENHA@localhost:5432/soomei"
```

Caracteres especiais da senha, como `@`, `:`, `/` e `#`, precisam ser codificados para uso em uma URL.

Crie as tabelas:

```bash
python -m api.db.create_tables
```

O projeto ainda não possui migrations versionadas. O comando acima cria as tabelas ausentes a partir dos models do SQLAlchemy.

### 4. Executar a API pública

Com o ambiente virtual ativo e as variáveis exportadas:

```bash
uvicorn api.app:create_app --factory --reload --port 8000
```

Acesse `http://localhost:8000`. Rotas úteis:

- `http://localhost:8000/onboard/abc123` — onboarding
- `http://localhost:8000/u/abc123` — cartão público
- `http://localhost:8000/q/abc123.png` — QR Code
- `http://localhost:8000/v/abc123.vcf` — vCard

### 5. Executar o admin

Abra outro terminal, volte à raiz do projeto e execute:

```bash
source .venv/bin/activate
export APP_ENV=dev
export DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/soomei"
export PUBLIC_BASE_URL="http://localhost:8000"
export ADMIN_HOST="localhost:8001,127.0.0.1:8001"
export ADMIN_EMAILS="seu-email@exemplo.com"
uvicorn api.admin_app:create_admin_app --factory --reload --port 8001
```

Acesse `http://localhost:8001/login`. O usuário precisa existir no banco, ter o e-mail verificado e estar listado em `ADMIN_EMAILS`. Sem essa variável, o fallback de desenvolvimento aceita e-mails `@soomei.com.br`.

### Inicialização diária resumida

Terminal da API pública:

```bash
source .venv/bin/activate
brew services start postgresql@18
export APP_ENV=dev
export DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/soomei"
export PUBLIC_BASE_URL="http://localhost:8000"
uvicorn api.app:create_app --factory --reload --port 8000
```

Terminal do admin:

```bash
source .venv/bin/activate
export APP_ENV=dev
export DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/soomei"
export PUBLIC_BASE_URL="http://localhost:8000"
export ADMIN_EMAILS="seu-email@exemplo.com"
uvicorn api.admin_app:create_admin_app --factory --reload --port 8001
```

## Variáveis de ambiente

Variáveis principais:

| Variável | Uso | Padrão |
| --- | --- | --- |
| `DATABASE_URL` | conexão SQLAlchemy com Postgres | obrigatória |
| `PUBLIC_BASE_URL` | URLs absolutas de QR, vCard e páginas | `https://soomei.cc` |
| `APP_ENV` | `dev` ou `prod`; ativa cookies seguros/HSTS em produção | `dev` |
| `ADMIN_EMAILS` | allowlist de administradores, separada por vírgulas | domínio `@soomei.com.br` |
| `ADMIN_HOST` | hosts aceitos pelo controle de origem do admin | localhost em dev |
| `ADMIN_SESSION_TTL_SECONDS` | duração da sessão administrativa | `43200` |
| `SESSION_TTL_SECONDS` | duração da sessão pública | `86400` |
| `CUSTOM_DOMAINS_ENABLED` | habilita fluxo de domínio personalizado | `false` |
| `SMTP_HOST`, `SMTP_PORT` | servidor de e-mail | vazio / `465` |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | credenciais e remetente SMTP | vazio |
| `EMAIL_VERIFICATION_TTL_SECONDS` | validade do token de confirmação | `900` |
| `PASSWORD_RESET_TTL` | validade do token de recuperação | `86400` |

As variáveis precisam ser exportadas antes de iniciar o processo, pois o projeto não carrega automaticamente um arquivo `.env`.

## Cartões e dados

Cadastre um cartão pendente (o PIN é gerado se for omitido):

```bash
python scripts/add_card.py --uid abc123
python scripts/add_card.py --uid abc124 --pin 654321 --vanity meu-nome
python scripts/add_card.py --uid abc125 --owner usuario@exemplo.com
```

Resete um cartão e gere um PIN novo:

```bash
python scripts/reset_card.py --uid abc123
python scripts/reset_card.py --uid abc123 --new-pin 654321
```

Importe uma única vez o antigo `api/data.json` para o Postgres:

```bash
python scripts/migrate_to_postgres.py
```

Antes desses comandos, mantenha `DATABASE_URL` exportada e o Postgres em execução.

## Testes

```bash
source .venv/bin/activate
export DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/soomei"
python -m pytest -q
```

## Cloudflare Worker

Instale e autentique o Wrangler:

```bash
npm install --global wrangler
wrangler login
```

Edite `cloudflare/wrangler.toml` e substitua o ID fictício do KV pelo ID real. Para desenvolvimento local:

```bash
cd cloudflare
wrangler dev --var API_BASE=http://localhost:8000
```

Para publicar:

```bash
cd cloudflare
wrangler deploy
```

O Worker ainda usa o KV `CARDS` como fonte no edge. Atualmente, alterações feitas no Postgres não são sincronizadas automaticamente com esse KV.

## Gravação NFC no macOS

Com um leitor USB compatível com `nfcpy` conectado:

```bash
source .venv/bin/activate
python -m pip install nfcpy
python scripts/write_tags.py scripts/slugs.csv
```

O CSV deve possuir uma coluna chamada `slug`:

```csv
slug
abc123
meu-nome
```

O script grava URLs no formato `https://soomei.cc/{slug}`. Para outro domínio, ajuste `scripts/write_tags.py` antes da gravação.

## Comandos úteis no macOS

Parar ou reiniciar o Postgres:

```bash
brew services stop postgresql@18
brew services restart postgresql@18
```

Abrir o banco no terminal:

```bash
psql -d soomei
```

Liberar uma porta ocupada:

```bash
python scripts/kill_port.py 8000
python scripts/kill_port.py 8001
```

Também é possível localizar o processo diretamente:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <PID>
```

Se a conexão ao banco falhar, confirme o serviço e a URL:

```bash
brew services list
psql -d soomei -c 'SELECT 1;'
echo "$DATABASE_URL"
```

## Versionamento e releases

O projeto usa SemVer (`vMAJOR.MINOR.PATCH`). Para criar uma release:

```bash
git switch main
git pull --ff-only
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main --follow-tags
```

O workflow de release do GitHub Actions é acionado por tag. Consulte `CHANGELOG.md` para o histórico.

## Contribuição

- Leia `AGENTS.md` para as diretrizes de arquitetura, segurança e deploy.
- Use commits no formato `tipo(escopo): resumo`.
- Preserve as rotas estáveis documentadas em `AGENTS.md`.
- Não versione `.venv/`, `web/uploads/`, segredos ou IDs reais de KV/Queue.
