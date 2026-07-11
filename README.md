# cont_chat_psipj — Chatwoot + Bot de Triagem (WhatsApp)

Atendimento via WhatsApp com triagem por IA:

```
WhatsApp (Cloud API oficial) → Chatwoot (pending) → [bot: triagem IA]
                                                       → open + time atribuído
                                                       → atendente se auto-atribui (pull)
```

## Estrutura

```
.
├── docker-compose.yml   # orquestra Chatwoot (rails+sidekiq+postgres+redis) + bot + Caddy
├── Caddyfile             # HTTPS automático (Let's Encrypt)
├── .env.example          # config do Chatwoot em si
├── SETUP.md              # guia completo de instalação na VPS, passo a passo
└── bot/                  # microserviço FastAPI de triagem por IA (Claude)
    ├── app/
    ├── Dockerfile
    ├── requirements.txt
    ├── .env.example
    └── README.md
```

## Começando

Siga o [SETUP.md](SETUP.md) — cobre desde criar a VPS até o WhatsApp
funcionando de ponta a ponta.

## Decisões de arquitetura

- **WhatsApp Cloud API oficial**, direto na Meta (sem BSP intermediário).
- **Chatwoot self-hosted**, via Docker Compose numa VPS (não é Chatwoot Cloud).
- **Modelo pull**: o bot faz a triagem e atribui a conversa a um **time**
  (sem agente específico); o atendente humano se auto-atribui da fila do
  time. O Chatwoot resolve a corrida de atribuição nativamente.
- **Bot stateless**: a cada mensagem, o histórico é reconstruído a partir da
  própria conversa do Chatwoot — sem banco de sessão próprio.
