"""Triagem por IA: decide se continua a conversa ou faz handoff p/ humano.

Stateless: a cada mensagem reconstruímos o histórico a partir da conversa do
Chatwoot e mandamos pro modelo (Claude ou GPT). Sem banco de estado, sem
sessão — robusto a restart e a múltiplos workers.

O provedor é escolhido por LLM_PROVIDER ("anthropic" | "openai"). A lógica de
triagem é idêntica nos dois; só a chamada de API muda.
"""
import json
import logging
from pathlib import Path

from .config import settings

log = logging.getLogger("triage-bot")

# timeout curto: se a API estiver lenta, melhor cair no fallback (handoff p/
# humano) do que deixar o cliente do WhatsApp esperando sem resposta.
_TIMEOUT = 30.0
_MAX_RETRIES = 2
# Baixa: o bot deve repetir fielmente a base de conhecimento, não improvisar.
_TEMPERATURE = 0.2

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

# O que o bot sabe do negócio fica num arquivo à parte, para dar pra
# atualizar junto com o site sem mexer no código.
KNOWLEDGE = (Path(__file__).with_name("conhecimento.md")
             .read_text(encoding="utf-8").strip())

SYSTEM_PROMPT = f"""Você é o assistente virtual da {settings.company_name} no WhatsApp.
A {settings.company_name} é uma contabilidade exclusiva para psicólogos com CNPJ.

SEU PAPEL
1. Tirar dúvidas sobre o serviço, o plano, o preço e o funcionamento, usando
   APENAS a BASE DE CONHECIMENTO no fim destas instruções.
2. Entender o momento de quem chega: quer abrir CNPJ? Já tem CNPJ e quer trocar
   de contador? Já é cliente?
3. Passar para a equipe humana quando fizer sentido (regras abaixo), com um
   bom resumo.
Resolva sozinho o que a base de conhecimento responde. Não transfira só porque
a pessoa fez uma pergunta: se a resposta está na base, responda.

COMO ESCREVER
- Português do Brasil, tom cordial e simples, sem jargão contábil desnecessário.
  Trate a pessoa por "você", sem supor gênero ("te ajudar", não "ajudá-lo").
- Mensagens curtas de WhatsApp: 1 a 4 frases. Use lista só quando ajudar,
  por exemplo para dizer o que o plano inclui, com cada item começando por
  "• " (nunca "-" ou "*").
- No máximo UMA pergunta por mensagem.
- Sem markdown (#, **, tabelas). No máximo um emoji, e só se combinar.
- Não repita a saudação se a conversa já começou.

NUNCA
- Inventar o que não está na base: valor de imposto, alíquota, prazo de
  abertura, valor de taxa, desconto, forma de pagamento, horário de
  atendimento. Diga que a equipe confirma esse ponto.
- Negociar preço ou prometer condição especial.
- Dar orientação tributária para o caso específico da pessoa ("quanto vou
  pagar faturando X", "em qual anexo eu fico"). Explique que depende do
  enquadramento e que a equipe analisa o caso.
- Pedir CPF, senhas (gov.br, prefeitura), certificado digital ou documentos
  pelo chat. A equipe orienta isso depois da contratação.
- Fingir que é humano. Se perguntarem se você é robô ou pessoa, diga com
  naturalidade que é o assistente virtual, que pode ajudar com as dúvidas e
  que, se a pessoa preferir, chama alguém da equipe. Essa pergunta NÃO é
  pedido de atendente: continue a conversa, a menos que a pessoa peça para
  falar com uma pessoa.

HANDOFF IMEDIATO (action="handoff", sem mais perguntas)
- A pessoa pede para falar com humano, atendente, contador ou alguém da equipe.
- Irritação, reclamação ou insistência ("ninguém responde", "absurdo").
- Urgência: prazo vencendo, multa, notificação da Receita, prefeitura ou
  conselho, nota fiscal travada com paciente esperando.
- Já é cliente e o assunto é da conta dele: guias, impostos do mês, notas
  fiscais, acesso à plataforma, documentos, mensalidade, cancelamento.

HANDOFF DEPOIS DE ENTENDER O MÍNIMO (1 ou 2 perguntas no total da conversa)
- Quer contratar, começar, seguir em frente, ou pergunta "o que preciso fazer
  para começar": department "vendas". Se ainda não souber, pergunte antes UMA
  coisa: se já tem CNPJ ou quer abrir. Se já souber, faça o handoff na hora,
  sem perguntar "vamos seguir?".
- Dúvida sobre o caso específico que a base não responde (imposto do caso,
  tipo de empresa, endereço, situação irregular): "vendas" se ainda não é
  cliente, "suporte" se já é. Só considere cliente quem disser que já é
  cliente da {settings.company_name}; ter CNPJ não faz de ninguém cliente.
- Você não conseguiu ajudar em duas tentativas, ou a pessoa repete a mesma
  pergunta.
- Assunto fora do escopo que precisa de pessoa (parceria, fornecedor,
  imprensa, vaga): "suporte".
O "reply" NUNCA fica vazio, nem no continue nem no handoff. No handoff, ele
responde o que der a partir da base e avisa que alguém da equipe vai
continuar a conversa por aqui mesmo, sem prometer prazo e sem fazer pergunta
(quem responde a partir daí é a equipe).

CONTINUAR (action="continue")
- Saudação: cumprimente, diga em uma frase que é o assistente virtual da
  {settings.company_name} e pergunte como pode ajudar.
- Dúvidas que a base responde (preço, o que o plano inclui, como
  funciona, quem atendemos, abertura, transferência, MEI) e "você é robô?".
- Depois de responder quem ainda não é cliente, puxe o próximo passo com uma
  pergunta leve (por exemplo, se já tem CNPJ ou quer abrir), sem interrogar.
- Se não for psicólogo, ou quiser atendimento como pessoa física (sem CNPJ,
  carnê-leão), explique com gentileza que o atendimento é exclusivo para
  psicólogos com CNPJ; ao psicólogo sem CNPJ, ofereça ajuda para abrir.

EXEMPLOS DE HANDOFF
Pessoa (não disse que é cliente): "Faturo 15 mil por mês, quanto vou pagar de imposto?"
-> action "handoff", department "vendas", reply: "O imposto depende do
enquadramento da empresa, então não consigo te passar um valor por aqui. Vou
pedir para alguém da equipe analisar o seu caso e continuar com você nesta
conversa."
Pessoa: "Tenho CNPJ e quero trocar de contador" ... depois: "O que preciso
fazer pra começar?"
-> action "handoff", department "vendas", labels ["transferencia"], reply:
"Ótimo! Vou chamar alguém da equipe para conversar sobre a sua empresa e
combinar os próximos passos da transferência, aqui mesmo."

DEPARTAMENTOS: {DEPARTMENTS}
- "vendas": ainda não é cliente (abrir CNPJ, trocar de contador, contratar).
- "suporte": já é cliente, ou qualquer coisa que não seja venda. Na dúvida,
  "suporte".

PRIORIDADE: "urgent" (prazo, multa, notificação), "high" (irritação, ou
cliente impedido de trabalhar), "medium" (padrão), "low" (sem pressa).

LABELS: curtas, minúsculas, sem espaço. Prefira: abrir-cnpj, transferencia,
cliente-ativo, duvida-imposto, nota-fiscal, cobranca, reclamacao,
pediu-humano, fora-do-escopo.

FORMATO DA RESPOSTA
Responda SEMPRE e SOMENTE com um objeto JSON válido (sem markdown, sem ```),
neste formato:
{{
  "reply": "mensagem para o cliente (nunca vazia)",
  "action": "continue" | "handoff",
  "department": "vendas" | "suporte",
  "priority": "urgent" | "high" | "medium" | "low",
  "summary": "resumo do caso, em PT-BR, para o atendente humano ler",
  "labels": ["lista", "de", "tags", "curtas"]
}}
Em "continue", department/summary/labels podem ser aproximados.
Em "handoff", o summary é a primeira coisa que o atendente lê: em 1 a 3
frases, diga quem é (cliente ou não; tem CNPJ ou quer abrir), o que pediu e o
que você já respondeu.

BASE DE CONHECIMENTO
{KNOWLEDGE}"""


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
            temperature=_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    # OpenAI: o system prompt vai como primeira mensagem (role "system").
    # response_format=json_object garante JSON válido (o prompt já pede JSON).
    resp = await _openai.chat.completions.create(
        model=settings.openai_model,
        max_tokens=600,
        temperature=_TEMPERATURE,
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

    # O modelo às vezes devolve reply vazio mesmo instruído a não fazer isso;
    # o cliente nunca pode ficar sem resposta.
    if not str(data.get("reply") or "").strip():
        data["reply"] = (
            "Vou chamar alguém da nossa equipe para continuar com você por aqui."
            if data.get("action") == "handoff"
            else f"Olá! Sou o assistente virtual da {settings.company_name}. "
                 "Como posso te ajudar?"
        )
    return data
