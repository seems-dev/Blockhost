"""Dedicated background worker entry point (subscription expiry, disk monitor, node watchdog)."""

from __future__ import annotations

import logging

from blockhost_backend.services.background_workers import run_background_workers_forever
from blockhost_backend.services.startup import bootstrap_application_data, bootstrap_database


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    bootstrap_database()
    bootstrap_application_data()
    run_background_workers_forever()


if __name__ == "__main__":
    main()
