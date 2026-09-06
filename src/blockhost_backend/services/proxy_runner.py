"""CLI entrypoint for the BlockHost game proxy.

Usage:
    blockhost-proxy

Runs the asyncio TCP+UDP proxy that routes player traffic to the correct
backend node based on the database routing table.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    logger = logging.getLogger("blockhost_proxy")

    from blockhost_backend.services.game_proxy import GameProxy

    proxy = GameProxy()

    async def run() -> None:
        await proxy.start()
        stop = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

        logger.info("BlockHost game proxy running — press Ctrl+C to stop")
        await stop.wait()
        await proxy.shutdown()

    asyncio.run(run())


if __name__ == "__main__":
    main()
