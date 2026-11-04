import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
GITIGNORE_PATH = ROOT / ".gitignore"


def ensure_gitignore():
    lines = GITIGNORE_PATH.read_text().splitlines() if GITIGNORE_PATH.exists() else []
    if ".env" not in lines:
        with GITIGNORE_PATH.open("a") as f:
            if lines and lines[-1].strip() != "":
                f.write("\n")
            f.write(".env\n")


def main():
    ensure_gitignore()

    if ENV_PATH.exists():
        answer = input(".env already exists. Overwrite? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print(f"Aborted. Existing file preserved: {ENV_PATH}")
            return

    client_id = input("PayPal Sandbox Client ID: ").strip()
    client_secret = getpass.getpass("PayPal Sandbox Client Secret: ").strip()

    content = (
        "PAYPAL_ENVIRONMENT=sandbox\n"
        f"PAYPAL_CLIENT_ID={client_id}\n"
        f"PAYPAL_CLIENT_SECRET={client_secret}\n"
        "PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com\n"
    )
    ENV_PATH.write_text(content)
    print(f"Created: {ENV_PATH}")
    print("Run: python scripts/check_paypal_sandbox.py")


if __name__ == "__main__":
    main()
