"""Webhook do Agent Bot do Chatwoot.

Fluxo:
  cliente manda msg no WhatsApp
    -> Chatwoot cria conversa em 'pending' e dispara 'message_created'
    -> este serviço roda a triagem com Claude
    -> 'continue': responde e segue em 'pending' (bot continua)
    -> 'handoff' : posta resumo (nota interna), aplica labels, prioridade,
                   atribui ao TIME e muda status p/ 'open'
    -> conversa cai na fila do time; um atendente se auto-atribui (pull)
"""
import asyncio
import hashlib
import hmac
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response

from .config import settings
from .chatwoot import Chatwoot
from .triage import triage, fallback_decision

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("triage-bot")

cw = Chatwoot()

# Um lock por conversa: mensagens rápidas do mesmo cliente são processadas
# uma de cada vez (evita triagens e respostas duplicadas). Vale porque o
# serviço roda num único processo; com múltiplos workers seria preciso um
# lock externo (ex.: Redis).
_conv_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await cw.aclose()


app = FastAPI(title="Chatwoot Triage Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _verify_signature(body: bytes, signature: str | None) -> bool:
    """Verifica a assinatura HMAC-SHA256 do webhook, se o secret estiver setado."""
    if not settings.chatwoot_bot_secret:
        return True  # verificação desligada
    if not signature:
        return False
    expected = hmac.new(
        settings.chatwoot_bot_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    sig = signature.split("=", 1)[-1]  # aceita formato "sha256=<hex>" ou "<hex>"
    return hmac.compare_digest(expected, sig)


@app.post("/webhook")
@app.post("/webhook/{token}")
async def webhook(
    request: Request, background: BackgroundTasks, token: str | None = None
) -> Response:
    # O Chatwoot não assina webhooks de Agent Bot; a autenticação real é o
    # token secreto embutido na outgoing_url (/webhook/<token>).
    if settings.webhook_token and token != settings.webhook_token:
        return Response(status_code=401)

    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Chatwoot-Signature")):
        return Response(status_code=401)

    payload = await request.json()

    # Só nos importam mensagens novas DO CLIENTE
    if payload.get("event") != "message_created":
        return Response(status_code=200)
    if payload.get("message_type") != "incoming":
        return Response(status_code=200)

    conv = payload.get("conversation", {}) or {}
    # Só agimos enquanto a conversa está com o bot (pending). Se um humano já
    # assumiu (open) ou foi resolvida, ficamos quietos.
    status = conv.get("status") or (conv.get("meta") or {}).get("status")
    if status and status != "pending":
        return Response(status_code=200)

    display_id = conv.get("display_id") or conv.get("id")
    if display_id is None:
        log.warning("payload sem display_id; ignorando")
        return Response(status_code=200)

    # 200 imediato: o Chatwoot dá timeout rápido em webhooks; o trabalho
    # pesado (Claude + chamadas de API) roda em background.
    background.add_task(_process, display_id, payload.get("id"))
    return Response(status_code=200)


async def _process(display_id: int, message_id: int | None) -> None:
    async with _conv_locks[display_id]:
        try:
            # Busca a conversa completa p/ ter o histórico confiável
            full = await cw.get_conversation(display_id)
            if full.get("status") not in (None, "pending"):
                return

            incoming = [
                m for m in full.get("messages", [])
                if m.get("message_type") == 0 and not m.get("private")
            ]

            # Debounce: se o cliente já mandou mensagem mais nova que a que
            # disparou este webhook, deixa só a última responder — quem manda
            # 3 mensagens seguidas recebe 1 resposta, não 3.
            if message_id is not None and incoming and incoming[-1].get("id") != message_id:
                return

            # Áudio/foto/anexo sem texto: o bot não entende mídia — avisa e
            # entrega direto a um humano em vez de responder no escuro.
            last = incoming[-1] if incoming else {}
            if last.get("attachments") and not (last.get("content") or "").strip():
                decision = fallback_decision(
                    reply=(
                        "Recebi seu arquivo/áudio! Vou te passar para um de "
                        "nossos atendentes, um momento."
                    ),
                    summary=(
                        "Cliente enviou mídia (áudio/imagem/arquivo) — "
                        "triagem automática não se aplica."
                    ),
                    labels=["midia"],
                )
            else:
                decision = await triage(full)

            log.info("conv %s -> action=%s dept=%s", display_id,
                     decision.get("action"), decision.get("department"))

            reply = (decision.get("reply") or "").strip()
            if reply:
                await cw.send_message(display_id, reply, private=False)

            if decision.get("action") == "handoff":
                await _handoff(display_id, decision)

        except Exception:
            log.exception("erro processando conversa %s", display_id)


async def _handoff(display_id: int, decision: dict) -> None:
    # Cada etapa é best-effort: uma falha isolada (ex.: team_id errado no
    # TEAM_MAP) não pode impedir o status de virar 'open' — senão a conversa
    # fica presa com o bot DEPOIS de ele já ter prometido a transferência.
    labels = decision.get("labels") or []
    if labels:
        try:
            await cw.add_labels(display_id, labels)
        except Exception:
            log.exception("falha ao aplicar labels")

    priority = decision.get("priority")
    if priority in ("urgent", "high", "medium", "low"):
        try:
            await cw.set_priority(display_id, priority)
        except Exception:
            log.exception("falha ao setar prioridade")

    # Resumo como nota interna (private) — primeira coisa que o atendente lê
    summary = decision.get("summary")
    if summary:
        try:
            await cw.send_message(display_id, f"🤖 Triagem: {summary}", private=True)
        except Exception:
            log.exception("falha ao postar nota interna")

    # Atribui ao time (sem agente) -> fila do time
    team_id = settings.teams.get(decision.get("department"))
    if team_id:
        try:
            await cw.assign_team(display_id, team_id)
        except Exception:
            log.exception("falha ao atribuir time (team_id=%s)", team_id)

    # Abre p/ humanos. Bot para de responder automaticamente nesta conversa.
    await cw.set_status(display_id, "open")
