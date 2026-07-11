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

    anthropic_api_key: str
    triage_model: str = "claude-sonnet-4-6"

    team_map: str = '{"geral": 1}'
    company_name: str = "Minha Empresa"

    @property
    def teams(self) -> dict[str, int]:
        return json.loads(self.team_map)


settings = Settings()
