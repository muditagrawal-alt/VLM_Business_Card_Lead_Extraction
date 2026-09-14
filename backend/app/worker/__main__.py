"""Entry point: `python -m app.worker`."""

from __future__ import annotations

import asyncio

from app.worker.runner import main

if __name__ == "__main__":
    asyncio.run(main())
