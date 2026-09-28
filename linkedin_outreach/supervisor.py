"""Crash supervisor for both outreach workflows."""
import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import time

from linkedin_outreach.config import load_config
from linkedin_outreach.paths import ROOT

LOG = logging.getLogger("linkedin-outreach.supervisor")
WORKERS = {
    "message": "linkedin_outreach.workflows.message",
    "add-contact": "linkedin_outreach.workflows.contacts",
}


def history_path(config, command):
    key = "history_file" if command == "message" else "connection_history_file"
    return ROOT / config[key]


def read_history(config, command="message"):
    path = history_path(config, command)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def completed(config, command):
    expected = "sent" if command == "message" else "invited"
    return sum(item.get("status") == expected for item in read_history(config, command).values())


def ready_to_restart(config, command="message"):
    if any(item.get("status") == "uncertain" for item in read_history(config, command).values()):
        LOG.error("Manual review is required because the last action is uncertain.")
        return False
    return True


def configured_limit(config, command):
    return config["max_messages"] if command == "message" else config["max_connection_requests"]


def run_supervised(command):
    os.umask(0o077)
    config = load_config(ROOT)
    log_directory = ROOT / config["log_directory"]
    log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_directory / "supervisor.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    lock_path = ROOT / f".{command}.supervisor.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.error("This workflow is already running.")
            return 2
        return _run_loop(config, command)


def _run_loop(config, command):
    baseline = completed(config, command)
    limit = configured_limit(config, command)
    failures = 0
    child = None
    try:
        while True:
            if not ready_to_restart(config, command):
                return 2
            progress = completed(config, command) - baseline
            remaining = max(0, limit - progress) if limit else 0
            if limit and remaining == 0:
                return 0
            environment = os.environ.copy()
            environment["OUTREACH_RUN_LIMIT"] = str(remaining)
            child = subprocess.Popen(
                [sys.executable, "-m", WORKERS[command]],
                cwd=ROOT,
                env=environment,
                start_new_session=True,
            )
            before = completed(config, command)
            code = child.wait()
            child = None
            after = completed(config, command)
            if code == 0:
                return 0
            if code == 2 or not ready_to_restart(config, command) or not config["auto_restart"]:
                return code
            failures = 0 if after > before else failures + 1
            if failures >= config["max_restarts_without_progress"]:
                LOG.error("Restart limit reached without progress.")
                return code
            delay = min(
                config["restart_delay_seconds"] * (2 ** max(0, failures - 1)),
                config["max_restart_delay_seconds"],
            )
            LOG.warning("Worker stopped; restarting in %s seconds.", delay)
            time.sleep(delay)
    except KeyboardInterrupt:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
        return 130
