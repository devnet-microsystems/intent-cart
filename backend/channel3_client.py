import httpx

from .config import settings


class Channel3Client:
    def __init__(self, timeout: float = 15.0):
        self.base_url = settings.channel3_base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str, filters: dict | None = None) -> list:
        if not settings.channel3_api_key:
            raise RuntimeError("CHANNEL3_API_KEY not configured")
        url = f"{self.base_url}/v1/search"
        headers = {"x-api-key": settings.channel3_api_key, "Content-Type": "application/json"}
        body = {"query": query, "config": {"country": "IT", "currency": "EUR"}}
        if filters:
            body["filters"] = filters
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data.get("products", []))
