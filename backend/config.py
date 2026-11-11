import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class Settings:
    def __init__(self):
        self.paypal_environment = os.getenv("PAYPAL_ENVIRONMENT", "sandbox")
        self.paypal_client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        self.paypal_client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.paypal_base_url = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
        self.channel3_api_key = os.getenv("CHANNEL3_API_KEY", "")
        self.channel3_base_url = os.getenv("CHANNEL3_BASE_URL", "https://api.trychannel3.com")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.frontend_base_url = os.getenv("FRONTEND_BASE_URL", "https://intent-cart-gamma.vercel.app")

    @property
    def missing_credentials(self) -> bool:
        return not self.paypal_client_id or not self.paypal_client_secret


settings = Settings()

# append env reads dynamically? Settings already instance. Add attributes manually not needed.
