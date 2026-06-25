from __future__ import annotations

import logging
import signal
import threading

from blockhost_backend.database.db import engine
from blockhost_backend.database.schema import Base
from blockhost_backend.orchestrator.backup import run_backup_scheduler_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger(__name__).info("Automatic backup scheduler is disabled.")
    # Automatic backup scheduler is disabled as per requirements.
    # Base.metadata.create_all(bind=engine)
    # stop_event = threading.Event()
    # def _request_stop(signum: int, _frame: object) -> None:
    #     logging.getLogger(__name__).info("Received signal %s; stopping backup scheduler", signum)
    #     stop_event.set()
    # signal.signal(signal.SIGTERM, _request_stop)
    # signal.signal(signal.SIGINT, _request_stop)
    # run_backup_scheduler_forever(stop_event=stop_event)



if __name__ == "__main__":
    main()
