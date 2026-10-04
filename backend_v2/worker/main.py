"""
Worker stub. Phase 0 scope: prove the container runs and stays healthy --
no jobs, no queue, no business logic. A later phase replaces the body of
the loop with real work (e.g. pulling from a task queue).
"""
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[worker] %(asctime)s %(message)s")
log = logging.getLogger("sentinel.worker")

HEARTBEAT_FILE = Path("/tmp/worker_heartbeat")


def main() -> None:
    log.info("worker stub starting -- no jobs configured yet")
    while True:
        HEARTBEAT_FILE.write_text(str(time.time()))
        log.info("heartbeat")
        time.sleep(10)


if __name__ == "__main__":
    main()
