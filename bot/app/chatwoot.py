"""Cliente fino para a Application API do Chatwoot.

Todas as chamadas usam o access_token do Agent Bot no header `api_access_token`.
O identificador de conversa nas URLs é o `display_id` (o número que aparece
no painel), NÃO o `id` interno — esse é o erro mais comum.
"""
import httpx
from .config import settings


class Chatwoot:
    def __init__(self) -> None:
        self.base = settings.chatwoot_base_url.rstrip("/")
        self.account_id = settings.chatwoot_account_id
        self.client = httpx.AsyncClient(
            headers={
                "api_access_token": settings.chatwoot_bot_token,
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )

    def _conv_url(self, conversation_id: int, suffix: str = "") -> str:
        return (
            f"{self.base}/api/v1/accounts/{self.account_id}"
            f"/conversations/{conversation_id}{suffix}"
        )

    async def get_conversation(self, conversation_id: int) -> dict:
        r = await self.client.get(self._conv_url(conversation_id))
        r.raise_for_status()
        return r.json()

    async def send_message(
        self, conversation_id: int, content: str, private: bool = False
    ) -> dict:
        """private=True vira nota interna (só agentes veem); útil p/ o resumo."""
        r = await self.client.post(
            self._conv_url(conversation_id, "/messages"),
            json={"content": content, "message_type": "outgoing", "private": private},
        )
        r.raise_for_status()
        return r.json()

    async def set_status(self, conversation_id: int, status: str) -> dict:
        """status: 'open' (handoff p/ humano), 'pending' (volta p/ bot), 'resolved'."""
        r = await self.client.post(
            self._conv_url(conversation_id, "/toggle_status"),
            json={"status": status},
        )
        r.raise_for_status()
        return r.json()

    async def set_priority(self, conversation_id: int, priority: str) -> None:
        # priority: 'urgent' | 'high' | 'medium' | 'low' | 'none'
        r = await self.client.post(
            self._conv_url(conversation_id, "/toggle_priority"),
            json={"priority": priority},
        )
        r.raise_for_status()

    async def assign_team(self, conversation_id: int, team_id: int) -> None:
        """Atribui ao TIME, sem agente -> cai na fila do time (modelo pull)."""
        r = await self.client.post(
            self._conv_url(conversation_id, "/assignments"),
            json={"team_id": team_id},
        )
        r.raise_for_status()

    async def add_labels(self, conversation_id: int, labels: list[str]) -> None:
        r = await self.client.post(
            self._conv_url(conversation_id, "/labels"),
            json={"labels": labels},
        )
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()
