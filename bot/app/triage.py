"""Triagem por IA: decide se continua a conversa ou faz handoff p/ humano.

Stateless: a cada mensagem reconstruímos o histórico a partir da conversa do
Chatwoot e mandamos pro modelo (Claude ou GPT). Sem banco de estado, sem
sessão — robusto a restart e a múltiplos workers.

O provedor é escolhido por LLM_PROVIDER ("anthropic" | "openai"). A lógica de
triagem é idêntica nos dois; só a chamada de API muda.
"""
import json
import logging

from .config import settings

log = logging.getLogger("triage-bot")

# timeout curto: se a API estiver lenta, melhor cair no fallback (handoff p/
# humano) do que deixar o cliente do WhatsApp esperando sem resposta.
_TIMEOUT = 30.0
_MAX_RETRIES = 2

PROVIDER = settings.provider
_anthropic = None
_openai = None
_init_error: str | None = None

if PROVIDER == "anthropic":
    if settings.anthropic_api_key:
        from anthropic import AsyncAnthropic

        _anthropic = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
        )
    else:
        _init_error = "LLM_PROVIDER=anthropic mas ANTHROPIC_API_KEY está vazio"
elif PROVIDER == "openai":
    if settings.openai_api_key:
        from openai import AsyncOpenAI

        _openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
        )
    else:
        _init_error = "LLM_PROVIDER=openai mas OPENAI_API_KEY está vazio"
else:
    _init_error = (
        f"LLM_PROVIDER inválido: {settings.llm_provider!r} "
        "(use 'anthropic' ou 'openai')"
    )

if _init_error:
    # Não derruba o serviço: loga e faz toda conversa cair no fallback (humano).
    log.error("Triagem por IA desabilitada — %s. Tudo será encaminhado a humano.",
              _init_error)

# Departamentos válidos = chaves do TEAM_MAP
DEPARTMENTS = list(settings.teams.keys())

SYSTEM_PROMPT = f"""Você é o atendente virtual de triagem da {settings.company_name}.
Seu trabalho é receber o cliente no WhatsApp, entender o que ele precisa e
encaminhar pro time humano certo. Você NÃO resolve o problema em si — você
qualifica e roteia. Seja cordial, objetivo e escreva em português do Brasil.

Faça no máximo 1-2 perguntas curtas para entender: (a) qual o assunto/setor e
(b) o contexto essencial. Assim que tiver isso, faça o handoff. Não enrole o
cliente com muitas perguntas.

Faça handoff IMEDIATO (sem mais perguntas) se: o cliente pedir explicitamente
para falar com um humano/atendente, demonstrar irritação, ou o caso for urgente.

Departamentos disponíveis: {DEPARTMENTS}
- "vendas": cliente novo ou existente interessado em comprar, planos, preços,
  orçamento, upgrade, renovação.
- "suporte": dúvidas, problemas técnicos, uso do produto, cobrança de cliente
  já pagante, reclamações, qualquer coisa que não seja claramente venda.

Se ficar em dúvida entre os dois, prefira "suporte".

Responda SEMPRE e SOMENTE com um objeto JSON válido (sem markdown, sem ```),
neste formato:
{{
  "reply": "mensagem para o cliente (string vazia se não quiser responder nada)",
  "action": "continue" | "handoff",
  "department": "vendas" | "suporte",
  "priority": "urgent" | "high" | "medium" | "low",
  "summary": "resumo de 1-2 frases do caso, em PT-BR, para o agente humano ler",
  "labels": ["lista", "de", "tags", "curtas"]
}}

Em "continue", 'department'/'summary'/'labels' podem ser aproximados.
Em "handoff", capriche no 'summary' — é o que o atendente vê primeiro."""


def fallback_decision(
    reply: str = "Vou te transferir para um de nossos atendentes, um momento.",
    summary: str = "Falha na triagem automática — encaminhado para humano.",
    labels: list[str] | None = None,
) -> dict:
    """Decisão segura quando a triagem por IA não pode/não deve rodar:
    manda para humano em vez de deixar o cliente sem resposta."""
    return {
        "reply": reply,
        "action": "handoff",
        "department": "suporte" if "suporte" in settings.teams else DEPARTMENTS[0],
        "priority": "medium",
        "summary": summary,
        "labels": labels if labels is not None else ["triagem-falhou"],
    }


def _history_to_messages(conversation: dict) -> list[dict]:
    """Converte as mensagens da conversa do Chatwoot no formato user/assistant.

    Esse formato é comum às duas APIs (Anthropic e OpenAI). message_type:
    0=incoming (cliente), 1=outgoing (bot/agente). Notas privadas e mensagens
    de sistema/atividade são ignoradas.
    """
    msgs = []
    for m in conversation.get("messages", []):
        if m.get("private"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        mtype = m.get("message_type")
        role = "user" if mtype == 0 else "assistant"
        msgs.append({"role": role, "content": content})
    # As APIs exigem começar com 'user'; descarta assistants iniciais órfãos
    while msgs and msgs[0]["role"] == "assistant":
        msgs.pop(0)
    return msgs or [{"role": "user", "content": "(cliente iniciou a conversa)"}]


async def _call_llm(messages: list[dict]) -> str:
    """Chama o provedor configurado e devolve o texto bruto da resposta."""
    if _anthropic is not None:
        resp = await _anthropic.messages.create(
            model=settings.anthropic_model,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    # OpenAI: o system prompt vai como primeira mensagem (role "system").
    # response_format=json_object garante JSON válido (o prompt já pede JSON).
    resp = await _openai.chat.completions.create(
        model=settings.openai_model,
        max_tokens=600,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
    )
    return (resp.choices[0].message.content or "").strip()


async def triage(conversation: dict) -> dict:
    # Provedor mal configurado (sem chave / valor inválido): handoff direto.
    if _init_error:
        return fallback_decision(
            summary=f"IA de triagem não configurada ({_init_error})."
        )

    messages = _history_to_messages(conversation)
    try:
        raw = await _call_llm(messages)
    except Exception:
        # API fora do ar / rate limit / timeout: sem fallback o bot ficaria
        # mudo e o cliente esperando — melhor entregar logo a um humano.
        log.exception("falha na chamada à API de triagem")
        return fallback_decision(
            summary="Falha na chamada à IA de triagem — encaminhado para humano."
        )

    # Defesa: tira cercas de markdown se o modelo escorregar (Claude às vezes;
    # OpenAI em modo json_object já vem limpo).
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        raw = raw.removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return fallback_decision()

    # Normaliza department para uma chave existente; "suporte" é o catch-all
    if data.get("department") not in settings.teams:
        data["department"] = "suporte" if "suporte" in settings.teams else DEPARTMENTS[0]
    return data
