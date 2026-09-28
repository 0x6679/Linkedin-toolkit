"""Configuration loading shared by the supervisor and worker."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

def load_config(base=BASE):
    path = base / "config.json"
    if not path.exists():
        raise FileNotFoundError("Copy config.example.json to config.json and configure it first.")
    config = json.loads(path.read_text(encoding="utf-8"))
    defaults = json.loads((BASE / "config.example.json").read_text(encoding="utf-8"))
    defaults.update(config)
    config = defaults
    keyword = config.get("people_search_keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("people_search_keyword must be a non-empty string")
    for key in ("send_messages", "login_only", "check_files_only", "headless", "refresh_contacts", "auto_restart", "use_connection_note"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    integers = ("max_messages", "max_connection_requests", "max_search_pages", "batch_size", "collection_stall_checks", "browser_timeout_ms", "upload_timeout_ms", "confirmation_timeout_ms", "navigation_timeout_ms", "assertion_timeout_ms", "navigation_attempts", "conversation_ready_attempts", "restart_delay_seconds", "max_restarts_without_progress", "max_restart_delay_seconds")
    for key in integers:
        minimum = 0 if key in ("max_messages", "max_connection_requests", "max_search_pages") else 1
        if type(config[key]) is not int or config[key] < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
    for key in ("scroll_wait_seconds", "collection_idle_timeout_seconds", "navigation_retry_wait_seconds"):
        if type(config[key]) not in (int, float) or config[key] <= 0:
            raise ValueError(f"{key} must be positive")
    return config
