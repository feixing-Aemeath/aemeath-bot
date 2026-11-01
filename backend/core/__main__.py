import logging

import uvicorn

from core.api import create_app
from core.config import Settings


def main():
    settings = Settings()
    # No access logs, SDK payload logs, or CLI output in application logs.
    for name in ("mcp", "httpx", "httpcore", "lark_oapi", "apscheduler"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        access_log=False,
        log_level="critical",
    )


if __name__ == "__main__":
    main()
