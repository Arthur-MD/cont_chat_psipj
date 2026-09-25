# Chatwoot Triage Bot

Microserviço de triagem por IA para Chatwoot. Recebe a conversa em `pending`,
qualifica com o Claude e faz handoff pro time humano certo, onde os atendentes
puxam da fila (modelo *pull*).

```
WhatsApp → Chatwoot (pending) → [este bot: triagem IA] → open + time atribuído
                                                          → atendente se auto-atribui
```

## 1. Pré-requisitos no Chatwoot

- Inbox de WhatsApp já conectada (Cloud API direto na Meta, via "Embedded Signup").
- Times criados em **Settings → Teams**: **Vendas** e **Suporte**.
  Anote o `team_id` de cada um — pegue na URL do time ou em
  `GET /api/v1/accounts/<id>/teams`.

## 2. Criar o Agent Bot (Rails console)

No servidor do Chatwoot (ou via `docker exec ... bundle exec rails console`):

```ruby
bot = AgentBot.create!(
  name: "Triagem IA",
  outgoing_url: "https://triagem.SEU-DOMINIO.com.br/webhook/SEU_WEBHOOK_TOKEN",
  account_id: <SEU_ACCOUNT_ID>            # bot da conta (não global)
)
puts bot.access_token.token               # <- copie p/ CHATWOOT_BOT_TOKEN

# conecta o bot ao inbox do WhatsApp
AgentBotInbox.create!(inbox: Inbox.find(<INBOX_ID>), agent_bot: bot)
```

> **Autenticação do webhook**: o Chatwoot não assina os webhooks de Agent
> Bot, então a proteção real é o token secreto embutido na própria URL.
> Gere um com `openssl rand -hex 16`, coloque em `WEBHOOK_TOKEN` no `.env`
> e use a mesma string na `outgoing_url` acima. Sem isso, qualquer pessoa
> que descubra a URL consegue mandar webhooks falsos pro bot.

A partir daqui, toda nova conversa nesse inbox nasce em `pending` e o Chatwoot
dispara `message_created` para a sua `outgoing_url`.

## 3. Configurar variáveis

Copie `.env.example` para `.env` e preencha. O `TEAM_MAP` liga os
"departamentos" que a IA escolhe aos `team_id` reais:

```
TEAM_MAP={"vendas": 1, "suporte": 2}
```

Substitua `1` e `2` pelos `team_id` reais dos times Vendas e Suporte criados
no passo 1. "suporte" é o catch-all: se a IA ficar em dúvida ou a triagem
falhar, a conversa cai lá. Os nomes das chaves são os mesmos que aparecem no
prompt de triagem — ajuste em `app/triage.py` se quiser adicionar outros
departamentos no futuro.

## O que o bot sabe

O que o bot pode afirmar sobre o negócio (plano, preço, o que está incluído,
FAQ) fica em `app/conhecimento.md`, que entra inteiro no prompt. Mantenha em
sincronia com o site: mudou o preço ou o FAQ lá, mude aqui e faça o redeploy
do `triagem-bot`. As regras de quando responder e quando passar para um humano
ficam no `SYSTEM_PROMPT` de `app/triage.py`.

## 4. Rodar local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# exponha p/ o Chatwoot testar: ngrok http 8000
```

## 5. Deploy em produção (VPS)

Em produção este bot roda **na mesma VPS do Chatwoot**, como o serviço
`triagem-bot` do `../docker-compose.yml` (exposto pelo Caddy em
`https://triagem.SEU-DOMINIO.com.br`). O passo a passo completo está em
`SETUP.md` na raiz do repo.

Como o bot e o Chatwoot compartilham a rede interna do Docker, use
`CHATWOOT_BASE_URL=http://rails:3000` no `.env` (já é o default do
`.env.example`).

## Como o handoff funciona

- **continue**: o bot responde e a conversa segue em `pending` (bot continua).
- **handoff**: posta o resumo como **nota interna**, aplica labels + prioridade,
  **atribui ao time** (sem agente) e muda status p/ `open`. A conversa some da
  fila do bot e aparece na fila do time. O bot para de responder.
- Um atendente abre a fila do time e se auto-atribui — esse é o seu *pull*.
- Para devolver ao bot, o atendente muda o status de volta p/ `pending`.

## Notas

- **Stateless**: a cada mensagem, o histórico é reconstruído da própria conversa
  do Chatwoot. Sem banco de sessão; seguro a restart e a múltiplos workers.
- **Janela de 24h (WhatsApp)**: como o fluxo é iniciado pelo cliente, as
  respostas caem na janela de serviço (grátis). Templates só seriam necessários
  se você reabrisse uma conversa fora das 24h.
- **Provedor de IA**: escolha via `LLM_PROVIDER` no `.env` — `anthropic`
  (Claude, padrão) ou `openai` (GPT). A lógica de triagem é idêntica; só a
  chamada de API muda. Só o provedor escolhido precisa da chave preenchida.
  - Anthropic: `ANTHROPIC_MODEL` (`claude-sonnet-4-6` padrão; troque por
    `claude-haiku-4-5-20251001` para alto volume mais barato).
  - OpenAI: `OPENAI_MODEL` (`gpt-4o-mini` padrão; `gpt-4o` para mais robustez).
