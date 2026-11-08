import base64
import logging
from typing import Optional

import httpx

import backend.config as config

logger = logging.getLogger("paypal_client")

TOKEN_PATH = "/v1/oauth2/token"


class PayPalAuthError(Exception):
    pass


class PayPalClient:
    def __init__(self, timeout: float = 10.0):
        self.base_url = config.settings.paypal_base_url.rstrip("/")
        self.timeout = timeout

    def _auth_header(self) -> str:
        if config.settings.missing_credentials:
            raise PayPalAuthError("PayPal credentials are not configured")
        creds = f"{config.settings.paypal_client_id}:{config.settings.paypal_client_secret}"
        encoded = base64.b64encode(creds.encode()).decode()
        return f"Basic {encoded}"

    async def get_access_token(self) -> str:
        url = f"{self.base_url}{TOKEN_PATH}"
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, data=data)
        except httpx.TimeoutException as exc:
            logger.error("PayPal token request timed out")
            raise PayPalAuthError("PayPal request timed out") from exc
        except httpx.HTTPError as exc:
            logger.error("PayPal HTTP error during token request")
            raise PayPalAuthError("PayPal connection error") from exc

        if response.status_code != 200:
            logger.error("PayPal token request failed with status %s", response.status_code)
            raise PayPalAuthError(f"PayPal authentication failed (status {response.status_code})")

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("Invalid JSON in PayPal token response")
            raise PayPalAuthError("Invalid response from PayPal") from exc

        token = payload.get("access_token")
        if not token:
            logger.error("PayPal response missing access_token")
            raise PayPalAuthError("Invalid response from PayPal")
        logger.info("PayPal access token obtained successfully")
        return token


    async def create_order(self, amount: str = "1.00", currency: str = "EUR") -> dict:
        if config.settings.paypal_environment != "sandbox":
            raise PayPalAuthError("Only sandbox mode is allowed")
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {"amount": {"currency_code": currency, "value": amount}}
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": "http://127.0.0.1:8001/checkout/return",
                        "cancel_url": "http://127.0.0.1:8001/checkout/cancel",
                        "user_action": "CONTINUE",
                    }
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            logger.error("PayPal order request timed out")
            raise PayPalAuthError("PayPal request timed out") from exc
        except httpx.HTTPError as exc:
            logger.error("PayPal HTTP error during order creation")
            raise PayPalAuthError("PayPal connection error") from exc

        if response.status_code not in (200, 201):
            logger.error("PayPal order creation failed with status %s", response.status_code)
            raise PayPalAuthError(f"PayPal order creation failed (status {response.status_code})")

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("Invalid JSON in PayPal order response")
            raise PayPalAuthError("Invalid response from PayPal") from exc

        order_id = payload.get("id")
        approve_url = None
        for link in payload.get("links", []):
            if link.get("rel") in ("payer-action", "approve"):
                approve_url = link.get("href")
                break
        if not order_id:
            logger.error("PayPal order response missing id")
            raise PayPalAuthError("Invalid response from PayPal")
        return {"order_id": order_id, "approve_url": approve_url}


    async def get_order(self, order_id: str) -> dict:
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            logger.error("PayPal get order timed out")
            raise PayPalAuthError("PayPal request timed out") from exc
        except httpx.HTTPError as exc:
            logger.error("PayPal HTTP error during get order")
            raise PayPalAuthError("PayPal connection error") from exc
        if response.status_code != 200:
            logger.error("PayPal get order failed with status %s", response.status_code)
            raise PayPalAuthError(f"PayPal get order failed (status {response.status_code})")
        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("Invalid JSON in PayPal order response")
            raise PayPalAuthError("Invalid response from PayPal") from exc
        return {"order_id": payload.get("id"), "status": payload.get("status")}


    async def capture_order(self, order_id: str, expected_amount: str = "1.00", expected_currency: str = "EUR") -> dict:
        if config.settings.paypal_environment != "sandbox":
            raise PayPalAuthError("Only sandbox mode is allowed")
        details = await self.get_order(order_id)
        if details.get("status") != "APPROVED":
            raise PayPalAuthError("Order is not in APPROVED state")
        # validate intent/amount via full order fetch
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise PayPalAuthError("Failed to fetch order details")
        payload = resp.json()
        if payload.get("intent") != "CAPTURE":
            raise PayPalAuthError("Invalid order intent")
        units = payload.get("purchase_units", [])
        if not units:
            raise PayPalAuthError("Missing purchase units")
        amt = units[0].get("amount", {})
        if amt.get("currency_code") != expected_currency or amt.get("value") != expected_amount:
            raise PayPalAuthError("Invalid order amount")

        capture_url = f"{self.base_url}/v2/checkout/orders/{order_id}/capture"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(capture_url, headers=headers, json={})
        except httpx.TimeoutException as exc:
            raise PayPalAuthError("PayPal request timed out") from exc
        except httpx.HTTPError as exc:
            raise PayPalAuthError("PayPal connection error") from exc
        if response.status_code not in (200, 201):
            raise PayPalAuthError(f"PayPal capture failed (status {response.status_code})")
        try:
            cap_payload = response.json()
        except ValueError as exc:
            raise PayPalAuthError("Invalid response from PayPal") from exc
        capture_amount = None
        capture_currency = None
        capture_id = None
        for pu in cap_payload.get("purchase_units", []):
            for cap in pu.get("payments", {}).get("captures", []):
                capture_id = cap.get("id")
                amt = cap.get("amount", {})
                capture_amount = amt.get("value")
                capture_currency = amt.get("currency_code")
        if not capture_id:
            raise PayPalAuthError("Invalid response from PayPal")
        return {"order_id": order_id, "status": cap_payload.get("status"), "capture_id": capture_id, "amount": capture_amount, "currency": capture_currency}
