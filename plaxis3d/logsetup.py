"""
plaxis3d.logsetup
=================

Configure the shared ``plaxis3d`` logger to write to the console *and* to a
per-params-file log at ``./log/<stem>.log`` (append mode, so history across runs
is preserved).
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("plaxis3d")

# directory (cwd-relative) where per-params-file logs are appended
LOG_DIR = "log"

_CONSOLE_FORMAT = "%(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(params_path: str, log_dir: str = LOG_DIR) -> str:
    """Route ``logger`` to console + ``<log_dir>/<params-stem>.log``.

    Existing handlers are cleared first, so repeated calls (e.g. several runs in
    one Python process) don't duplicate log lines. Returns the log file path.
    """
    stem = os.path.splitext(os.path.basename(params_path))[0]
    os.makedirs(log_dir, exist_ok=True)
    logfile = os.path.join(log_dir, stem + ".log")

    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(logfile, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(console)
    return logfile
