"""Local/LAN entrypoint that reads host and port from the application settings."""

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        access_log=True,
    )


if __name__ == "__main__":
    main()
