"""Run the post-call webhook service locally with INFO logging to postcall.log.

Usage:
    python scripts/run_webhook.py            # 127.0.0.1:8010
    python scripts/run_webhook.py --port 9000 --host 0.0.0.0

Loads RETELL_WEBHOOK_KEY (and the rest) from .env. Fails closed if the key is
missing. Our application loggers and uvicorn's own logs both land in postcall.log
(uvicorn's log_config is disabled so the root FileHandler wins).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from dma_deploy_kit.postcall.service import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / "postcall.log"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the post-call webhook service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_dotenv(REPO_ROOT / ".env")

    app = create_app()  # raises if RETELL_WEBHOOK_KEY is missing (fail closed)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", log_config=None)


if __name__ == "__main__":
    main()
