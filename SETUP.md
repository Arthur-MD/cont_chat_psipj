# Chatwoot self-hosted na Hetzner (VPS + Docker Compose) — guia passo a passo

Este guia sobe o Chatwoot + o bot de triagem numa única VPS, usando os
arquivos deste repositório: `docker-compose.yml` e `Caddyfile` na raiz, e o
código do bot em `bot/`. Ao final você terá o painel do Chatwoot e o bot
rodando com HTTPS automático.

Repositório: https://github.com/Arthur-MD/cont_chat_psipj

> **Atenção — a instalação em produção do psicologopj.com.br não segue mais
> este guia à risca.** Desde julho/2026 o Chatwoot divide a VPS com a
> plataforma contábil, e quem termina as portas 80/443 e emite os
> certificados é o Caddy daquela stack. Por isso o `docker-compose.yml`
> deste repo **não sobe mais um Caddy nem publica portas**: os serviços
> `rails` e `triagem-bot` entram na rede Docker externa `contasofia_edge`
> com os aliases `chatwoot-rails` e `triagem-bot`, e é por esses nomes que
> o proxy os alcança. Consequências práticas para os passos abaixo:
>
> - a rede `contasofia_edge` precisa existir antes do `docker compose up`
>   (ela é criada pela stack principal);
> - o `Caddyfile` da raiz deste repo ficou como referência para uma
>   instalação autônoma — em produção o roteamento de `chat.` e `triagem.`
>   vive no Caddyfile da stack principal;
> - o DNS dos subdomínios aponta para a VPS compartilhada, não para o IP
>   citado nos exemplos deste guia.
>
> Para uma instalação isolada (uma VPS só para o Chatwoot), reative o
> serviço `caddy` e os `ports` no compose e siga o guia sem ressalvas.

## Pré-requisito: DNS do domínio

Domínio: `psicologopj.com.br` (Registro.br, DNS gerenciado lá mesmo —
nameservers `sec.dns.br`). VPS: `167.233.169.248` (Hetzner CX23, Nuremberg).

No painel do Registro.br → seu domínio → aba **DNS** → crie **dois
registros tipo A**:

```
chat.psicologopj.com.br      ->  167.233.169.248
triagem.psicologopj.com.br   ->  167.233.169.248
```

No campo "Nome"/"Host" geralmente basta digitar `chat` e `triagem` (sem o
domínio completo) — o painel já entende que vira `chat.psicologopj.com.br`.

A propagação do DNS pode levar de minutos a algumas horas. Para conferir se
já propagou, rode (no seu computador ou na VPS):
```bash
nslookup chat.psicologopj.com.br
```
Se retornar `167.233.169.248`, já propagou e pode seguir para o Passo 5.

## Passo 1 — Criar a VPS

Você já fez isso (CX23, Ubuntu, 4GB RAM). Só confirme antes de finalizar:

- ✅ Adicionou uma **chave SSH** (se ainda não tem uma, veja a seção
  "Gerando uma chave SSH" no fim deste guia).
- ✅ Marcou o app **"Docker CE"** no marketplace (isso já deixa o Docker
  instalado quando o servidor nascer — poupa um passo manual). Se você já
  criou o servidor sem isso, sem problema, instale depois (passo 2).
- ✅ Criou um **Firewall** liberando só as portas 22 (SSH), 80 (HTTP) e 443
  (HTTPS).

## Passo 2 — Conectar na VPS e conferir o Docker

```bash
ssh root@167.233.169.248
```

Se marcou "Docker CE" na criação, já está instalado — confirme:

```bash
docker --version
docker compose version
```

Se não apareceu nada, instale manualmente:

```bash
curl -fsSL https://get.docker.com | sh
```

## Passo 3 — Clonar o repositório

```bash
cd /opt
git clone https://github.com/Arthur-MD/cont_chat_psipj.git
cd cont_chat_psipj
```

> Se o repo for privado, você vai precisar configurar acesso (chave SSH
> própria do servidor associada à sua conta Git, ou um token de acesso).

## Passo 4 — Configurar as variáveis de ambiente

```bash
cp .env.example .env
nano .env
```

Preencha, no mínimo:

- `SECRET_KEY_BASE` — gere com `openssl rand -hex 64`
- `FRONTEND_URL` — `https://chat.psicologopj.com.br` (o domínio real)
- `POSTGRES_PASSWORD` — gere com `openssl rand -hex 24`

Depois, configure o bot de triagem:

```bash
cd bot
cp .env.example .env
nano .env
```

Preencha `ANTHROPIC_API_KEY` e `TEAM_MAP`, e gere o token de autenticação do
webhook com `openssl rand -hex 16`, colando o resultado em `WEBHOOK_TOKEN`
(guarde esse valor — ele entra na URL do Agent Bot no passo 8). Deixe
`CHATWOOT_BOT_TOKEN` em branco por enquanto — você só vai ter esse token no
passo 8.

O `Caddyfile` já vem configurado com `chat.psicologopj.com.br` e
`triagem.psicologopj.com.br` — não precisa editar nada nele, a menos que
você troque de domínio no futuro.

## Passo 5 — Subir tudo

```bash
cd /opt/cont_chat_psipj
docker compose up -d
```

Isso vai:
1. Baixar as imagens (Chatwoot, Postgres, Redis, Caddy)
2. Rodar `rails-prepare` (cria as tabelas do banco) — só executa uma vez e sai
3. Subir Rails, Sidekiq, o bot de triagem e o Caddy
4. O Caddy tenta emitir certificado HTTPS automaticamente (por isso o DNS
   precisa já estar apontando)

Acompanhe os logs até tudo estabilizar:

```bash
docker compose logs -f
```

(`Ctrl+C` sai do modo de acompanhamento sem parar os containers)

## Passo 6 — Criar sua conta admin (e trancar o cadastro)

1. Abra `https://chat.psicologopj.com.br` no navegador.
2. Crie sua conta (você vira o administrador).
3. Volte no `.env` do Chatwoot e mude:
   ```
   ENABLE_ACCOUNT_SIGNUP=false
   ```
4. Aplique a mudança:
   ```bash
   docker compose up -d rails sidekiq
   ```

## Passo 7 — Criar os times Vendas e Suporte

1. No Chatwoot: **⚙ Settings → Teams → Create new team**
2. Crie `Vendas` e `Suporte`, marcando os atendentes de cada um.
3. Anote o `team_id` de cada um: clique no time e olhe a URL
   (`.../settings/teams/7/edit` → o id é `7`).
4. Volte no `.env` de `bot/` e preencha `TEAM_MAP` com os ids reais:
   ```
   TEAM_MAP={"vendas": 7, "suporte": 12}
   ```

## Passo 8 — Criar o Agent Bot

O Agent Bot é o "usuário robô" que o `triagem-bot` usa para falar com o
Chatwoot. Cria-se pelo console Rails, direto de dentro do container:

```bash
cd /opt/cont_chat_psipj
docker compose exec rails bundle exec rails c
```

Dentro do console Rails que abrir:

```ruby
bot = AgentBot.create!(
  name: "Triagem IA",
  # o WEBHOOK_TOKEN é o mesmo que você gerou no passo 4 (bot/.env)
  outgoing_url: "https://triagem.psicologopj.com.br/webhook/SEU_WEBHOOK_TOKEN",
  account_id: 1
)
puts bot.access_token.token   # <- copie: é o CHATWOOT_BOT_TOKEN

# conecta o bot ao inbox do WhatsApp (crie o inbox primeiro, passo 9)
AgentBotInbox.create!(inbox: Inbox.find(ID_DO_INBOX), agent_bot: bot)
```

Saia do console com `exit`. Cole o token copiado no `bot/.env`:

```bash
cd bot
nano .env   # CHATWOOT_BOT_TOKEN=<cole aqui>
```

Reinicie o bot para aplicar:

```bash
cd ..
docker compose up -d triagem-bot
```

## Passo 9 — Conectar o WhatsApp (Cloud API)

1. No Chatwoot: **⚙ Settings → Inboxes → Add Inbox → WhatsApp**.
2. Escolha **WhatsApp Cloud** e siga o "Embedded Signup" — abre um popup da
   Meta onde você loga com a conta do Facebook Business, escolhe/cria o
   número e autoriza. O Chatwoot configura os webhooks da Meta sozinho.
3. Pré-requisitos do lado da Meta: um Facebook Business verificado e um
   número de telefone "limpo" (não registrado em WhatsApp comum/Business app).
4. Depois de criar o inbox, anote o ID dele (na URL de Settings → Inboxes →
   clique no inbox) e volte no passo 8 para rodar o `AgentBotInbox.create!`
   se ainda não fez.

Fluxo completo funcionando: mensagem no WhatsApp → Chatwoot cria conversa
`pending` → chama o bot → bot responde/triagem → handoff pro time → atendente
puxa da fila.

## Manutenção do dia a dia

**Ver logs de um serviço específico:**
```bash
docker compose logs -f sidekiq
```

**Reiniciar depois de mudar algo no .env:**
```bash
docker compose up -d
```

**Atualizar a versão do Chatwoot:** troque a tag da imagem no
`docker-compose.yml` (ex.: `chatwoot/chatwoot:v4.15.1` → versão nova; veja as
tags em hub.docker.com/r/chatwoot/chatwoot/tags) e rode:
```bash
docker compose pull
docker compose up -d
```
(o `rails-prepare` roda de novo automaticamente e aplica migrations novas)

**Backup do banco** (faça isso periodicamente — no Render seria automático,
aqui é manual):
```bash
docker compose exec postgres pg_dump -U postgres chatwoot > backup-$(date +%F).sql
```
Copie esse arquivo para fora da VPS (seu computador, ou um bucket S3) —
backup que só existe na própria máquina que pode falhar não protege nada.

## Gerando uma chave SSH (se ainda não tem uma)

No seu computador (não na VPS):

```bash
ssh-keygen -t ed25519 -C "seu-email@exemplo.com"
```

Aceite o caminho padrão. Isso cria dois arquivos: uma chave privada (fica no
seu computador, nunca compartilhe) e uma pública (`.pub`, essa você cola no
campo "Add SSH key" da Hetzner). Para ver o conteúdo da pública:

```bash
cat ~/.ssh/id_ed25519.pub
```

## Opcional — Mídia com Cloudflare R2

O volume Docker `storage_data` guarda as fotos/áudios do WhatsApp
localmente — funciona, mas não escala bem nem tem backup fácil. Para produção
séria, considere um bucket S3-compatível (R2 da Cloudflare tem 10GB grátis):

```
ACTIVE_STORAGE_SERVICE=s3_compatible
STORAGE_BUCKET_NAME=<bucket>
STORAGE_ACCESS_KEY_ID=<key>
STORAGE_SECRET_ACCESS_KEY=<secret>
STORAGE_REGION=auto
STORAGE_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
STORAGE_FORCE_PATH_STYLE=true
```

Confira os nomes exatos das variáveis na doc oficial do Chatwoot antes de
aplicar — podem variar entre versões.

## Troubleshooting

- **Caddy não emite certificado HTTPS**: confirme que o DNS já propagou
  (`dig chat.seudominio.com.br` deve mostrar o IP da VPS) e que as portas 80
  e 443 estão liberadas no Firewall da Hetzner.
- **`rails-prepare` falhou**: veja `docker compose logs rails-prepare` — na
  maioria das vezes é senha do Postgres divergente entre `.env` e o volume
  já criado (se você mudou a senha depois do primeiro `up`, apague o volume
  `postgres_data` só se ainda não tiver dados importantes).
- **Painel abre mas mensagens não fluem**: verifique
  `docker compose logs sidekiq` — é ele quem processa as mensagens em
  background.
- **Sem RAM** (containers reiniciando sozinhos): rode `docker stats` para
  ver o consumo real; se estourar os 4GB da CX23 com uso real, é hora de
  subir pra CX33/CX43.
