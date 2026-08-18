import os

from pydantic import BaseModel, Field


class WebhookSettings(BaseModel):
    """Configuration for webhook notifications."""
    discord_webhook_url: str = Field(
        default="", 
        description="Discord Webhook URL (env: DISCORD_WEBHOOK_URL)"
    )
    slack_webhook_url: str = Field(
        default="", 
        description="Slack Webhook URL (env: SLACK_WEBHOOK_URL)"
    )

def get_webhook_settings() -> WebhookSettings:
    """Construct webhook settings from the current environment."""
    return WebhookSettings(
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
    )
