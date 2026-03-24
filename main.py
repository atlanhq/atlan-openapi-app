"""Entry point for the OpenAPI Connector app.

Starts a combined HTTP handler + Temporal worker for production/container use.
For local development with example curl commands, use ``app/run_dev.py`` instead.

Usage:
    python main.py
"""

import asyncio

from app.run_dev import main

if __name__ == "__main__":
    asyncio.run(main())
