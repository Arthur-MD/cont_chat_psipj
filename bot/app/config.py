import json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chatwoot_base_url: str = "https://app.chatwoot.com"
    chatwoot_account_id: int = 1
    chatwoot_bot_token: str
    chatwoot_bot_secret: str = ""
    # Token secreto embutido na outgoing_url do Agent Bot
    # (ex.: https://triagem.dominio/webhook/<token>). O Chatwoot não assina
    # webhooks de Agent Bot, então este é o mecanismo real de autenticação.
    webhook_token: str = ""

    # Token de um usuário AGENTE (Perfil > Configurações de Perfil > Token de
    # acesso) — não é o token do Agent Bot, que não pode listar conversas nem
    # ler o histórico de mensagens. Sem ele, o bot só enxerga a última
    # mensagem do cliente e a devolução automática fica desligada.
    chatwoot_admin_token: str = ""

    # Conversas 'open' (com humano) sem nenhuma atividade por esse tempo
    # voltam sozinhas para 'pending' (bot), sem avisar o cliente.
    auto_return_hours: float = 24.0
    # De quanto em quanto tempo o bot verifica as conversas (minutos).
    auto_return_check_minutes: int = 15
    # Restringe a checagem a um inbox específico. Vazio = todos os inboxes.
    auto_return_inbox_id: int | None = None

    # --- Provedor de IA para a triagem ---
    # "anthropic" (Claude) ou "openai" (GPT). Só o provedor escolhido precisa
    # ter a chave preenchida; o outro pode ficar vazio.
    llm_provider: str = "anthropic"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    # Modelos de raciocínio (gpt-5*, gpt-6*): none | low | medium | high.
    # Deixe VAZIO para modelos comuns (gpt-4.1-mini, gpt-4o-mini).
    openai_reasoning_effort: str = "low"

    team_map: str = '{"geral": 1}'
    company_name: str = "Minha Empresa"

    @property
    def teams(self) -> dict[str, int]:
        return json.loads(self.team_map)

    @property
    def provider(self) -> str:
        return self.llm_provider.strip().lower()


settings = Settings()
