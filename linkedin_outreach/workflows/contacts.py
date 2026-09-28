#!/usr/bin/env python3
"""Invite people from a paginated LinkedIn people search and persist every decision."""
import asyncio
import fcntl
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode, urlunsplit

from linkedin_outreach.config import load_config
from linkedin_outreach.pages.login_page import LoginPage
from linkedin_outreach.pages.people_search_page import PeopleSearchPage
from linkedin_outreach.paths import ROOT as BASE
from linkedin_outreach.storage import load_json, save_json
from linkedin_outreach.workflows import message as shared

LOG = shared.LOG
PROFILE_RE = re.compile(r"^https://www\.linkedin\.com/in/([^/?#]+)")


def page_url(keyword, page):
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("The people-search keyword must be a non-empty string.")
    if type(page) is not int or page < 1:
        raise ValueError("The search page must be a positive integer.")
    query = urlencode(
        {
            "keywords": keyword.strip(),
            "origin": "CLUSTER_EXPANSION",
            "page": page,
            "spellCorrectionEnabled": "true",
            "prioritizeMessage": "false",
        }
    )
    return urlunsplit(("https", "www.linkedin.com", "/search/results/people/", query, ""))


def load_note(config):
    if not config["use_connection_note"]:
        return None
    path = BASE / config["notes_file"]
    if not path.exists():
        LOG.info("No notes file; invitations will be sent without notes")
        return None
    note = path.read_text(encoding="utf-8").strip()
    if not note:
        LOG.info("Empty notes file; invitations will be sent without notes")
        return None
    if len(note) > 200:
        raise ValueError(f"Connection note is {len(note)} characters; LinkedIn limit is 200")
    return note


def profile_identity(href):
    match = PROFILE_RE.match(href.split("?")[0].rstrip("/") + "/")
    return match.group(1) if match else None


async def result_cards(page):
    search_page = PeopleSearchPage(page, 30000)
    snapshot = await search_page.profile_snapshot()
    results = []
    for entry in snapshot:
        href = entry["href"]
        if not search_page.DEGREE_TEXT.search(entry["text"]):
            continue
        if href and href.startswith("/in/"):
            href = "https://www.linkedin.com" + href
        ident = profile_identity(href or "")
        if not ident or any(item["id"] == ident for item in results):
            continue
        card = search_page.result_card(entry["href"])
        if not await card.count():
            continue
        name = entry["text"].strip().split("\n")[0] or ident
        results.append({"id": ident, "name": name, "url": href.split("?")[0], "card": card})
    return results


async def save_known(person, status, known):
    known[person["id"]] = {
        "name": person["name"], "url": person["url"], "status": status,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


async def sign_in(page, config):
    login_page = LoginPage(page, config["browser_timeout_ms"])
    await login_page.sign_in(config.get("email"), config.get("password"))


async def invite(person, note, history, known, config):
    card = person["card"]
    search_page = PeopleSearchPage(card.page, config["browser_timeout_ms"])
    connect = search_page.connect_action(card)
    message = search_page.message_action(card)
    pending = search_page.pending_label(card)
    if await connect.count() != 1:
        status = "already_connected" if await message.count() else "pending" if await pending.count() else "no_connect_button"
        await save_known(person, status, known)
        return False

    await connect.click()
    dialog = search_page.invitation_dialog()
    await dialog.wait_for(state="visible", timeout=config["browser_timeout_ms"])
    add_note = search_page.add_note_button(dialog)
    note_will_be_used = bool(note and await add_note.count() and await add_note.is_visible())
    if note_will_be_used:
        await add_note.click()
        field = search_page.note_editor(dialog)
        await field.fill(note)
    send = search_page.send_invitation_button(dialog)
    await send.wait_for(state="visible", timeout=config["browser_timeout_ms"])
    record = {
        "name": person["name"], "url": person["url"], "status": "uncertain",
        "timestamp": datetime.now(timezone.utc).isoformat(), "note_used": note_will_be_used,
    }
    history[person["id"]] = record
    save_json(BASE / config["connection_history_file"], history)
    await send.click()
    await dialog.wait_for(state="hidden", timeout=config["confirmation_timeout_ms"])
    record["status"] = "invited"
    await save_known(person, "invited", known)
    save_json(BASE / config["connection_history_file"], history)
    LOG.info("INVITE_CONFIRMED name=%s id=%s note=%s", person["name"], person["id"], record["note_used"])
    return True


async def run(config):
    from playwright.async_api import async_playwright
    note = load_note(config)
    history_path = BASE / config["connection_history_file"]
    known_path = BASE / config["known_contacts_file"]
    history, known = load_json(history_path), load_json(known_path)
    if any(item.get("status") == "uncertain" for item in history.values()):
        raise RuntimeError("An earlier invitation is uncertain. Review it before resuming.")
    sent = 0
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(BASE / config["browser_profile_directory"]), headless=config["headless"])
        context.set_default_timeout(config["browser_timeout_ms"])
        try:
            await shared.restore_cookies(context)
            page = context.pages[0] if context.pages else await context.new_page()
            first_url = page_url(config["people_search_keyword"], 1)
            await shared.open_page(page, first_url)
            if "/login" in page.url or "/uas/" in page.url:
                LOG.info("Saved session expired; signing in from private config")
                await sign_in(page, config)
                if any(part in page.url for part in ("/checkpoint", "/challenge")):
                    raise RuntimeError("LinkedIn security verification requires manual action.")
                await shared.open_page(page, first_url)
            for number in range(1, (config["max_search_pages"] or 10_000) + 1):
                await shared.open_page(page, page_url(config["people_search_keyword"], number))
                if any(part in page.url for part in ("/login", "/checkpoint", "/challenge", "/uas/")):
                    raise RuntimeError("LinkedIn login or security verification is required.")
                await page.wait_for_load_state("domcontentloaded")
                try:
                    search_page = PeopleSearchPage(page, config["browser_timeout_ms"])
                    await search_page.wait_until_ready()
                except Exception:
                    LOG.error("Search results or action buttons did not render page=%s url=%s", number, page.url)
                    await shared.browser_diagnostics(page)
                    raise RuntimeError(
                        "LinkedIn did not render people and action buttons before the timeout."
                    ) from None
                cards = await result_cards(page)
                LOG.info("PAGE page=%s people=%s url=%s", number, len(cards), page.url)
                if not cards:
                    raise RuntimeError("Profile links loaded, but no result cards could be identified.")
                for person in cards:
                    if person["id"] in history:
                        continue
                    did_send = await invite(person, note, history, known, config)
                    save_json(known_path, known)
                    sent += int(did_send)
                    if config["max_connection_requests"] and sent >= config["max_connection_requests"]:
                        return sent
                next_button = search_page.next_button()
                if await next_button.count() != 1 or await next_button.is_disabled():
                    break
            return sent
        finally:
            await shared.save_cookies(context)
            await context.close()


def main():
    os.umask(0o077)
    config = load_config(BASE)
    shared.CONFIG = config
    shared.setup_logging()
    if "OUTREACH_RUN_LIMIT" in os.environ:
        limit = int(os.environ["OUTREACH_RUN_LIMIT"])
        if limit < 0:
            raise RuntimeError("The supervisor invitation budget is invalid.")
        config = dict(config, max_connection_requests=limit)
    with (BASE / ".contact-adder.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("The contact adder is already running.") from None
        sent = asyncio.run(run(config))
        LOG.info("Contact workflow completed with %s new invitations.", sent)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        LOG.exception("CONTACT_RUN_FAILED")
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
