import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.paypal_client import PayPalClient, PayPalAuthError


async def main():
    try:
        client = PayPalClient()
        await client.get_access_token()
        print("PAYPAL SANDBOX: CONNECTION OK")
    except PayPalAuthError as exc:
        print(f"PAYPAL SANDBOX: ERROR - {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
