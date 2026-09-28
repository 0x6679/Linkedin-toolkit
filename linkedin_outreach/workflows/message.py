#!/usr/bin/env python3
"""Send message.txt and cv.pdf to LinkedIn connections, with a durable journal."""
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

from linkedin_outreach.config import load_config
from linkedin_outreach.pages.connections_page import ConnectionsPage
from linkedin_outreach.pages.login_page import LoginPage
from linkedin_outreach.paths import ROOT as BASE

CONFIG = {}
URL = CONFIG.get('connections_url', 'https://www.linkedin.com/mynetwork/invite-connect/connections/')
LINK_NAME = ConnectionsPage.MESSAGE_LINK_NAME
LOG = logging.getLogger('linkedin-outreach')
STAGE = 'startup'


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__('%(asctime)s %(levelname)s %(message)s')
        self.secrets = secrets

    def format(self, record):
        text = super().format(record)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, '[REDACTED]')
        return text


def setup_logging():
    os.umask(0o077)
    folder = BASE / CONFIG.get('log_directory', 'logs')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    secrets = []
    try:
        config = json.loads((BASE / 'config.json').read_text())
        secrets = [str(config.get(k, '')) for k in ('password', 'email')]
    except Exception:
        pass
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = folder / f'run-{stamp}-{os.getpid()}.log'
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(RedactingFormatter(secrets))
    LOG.setLevel(logging.DEBUG)
    LOG.addHandler(handler)
    LOG.info('Run started pid=%s python=%s platform=%s args=%s', os.getpid(), platform.python_version(), platform.system(), sys.argv[1:])
    LOG.info('Log file: %s', path)
    return path


def stage(name, **details):
    global STAGE
    STAGE = name
    LOG.info('STAGE %s %s', name, json.dumps(details, ensure_ascii=False))


async def browser_diagnostics(page):
    # No input values, cookies, message bodies, HTML dumps or credential screenshots.
    try:
        parts = urlsplit(page.url)
        counts = await ConnectionsPage(page).diagnostic_counts()
        LOG.error('Browser state: host=%s path=%s title=%s frames=%s', parts.hostname, parts.path, await page.title(), len(page.frames))
        LOG.error('Visible controls: editors=%s sends=%s attachments=%s',
                  counts['editors'], counts['sends'], counts['attachments'])
    except Exception as exc:
        LOG.error('Diagnostics unavailable: %s', type(exc).__name__)




async def restore_cookies(context):
    path = BASE / CONFIG.get('cookies_file', 'cookies.json')
    if not path.exists():
        LOG.info('No separate cookie file; using saved browser profile')
        return
    cookies = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(cookies, list) or any(not (c.get('domain', '').lstrip('.') == 'linkedin.com' or c.get('domain', '').lstrip('.').endswith('.linkedin.com')) for c in cookies):
        raise RuntimeError('The LinkedIn cookie file is invalid.')
    valid = [c for c in cookies if c.get('expires', -1) <= 0 or c['expires'] > time.time()]
    await context.add_cookies(valid)
    LOG.info('Cookies restored count=%s expired_skipped=%s', len(valid), len(cookies)-len(valid))


async def save_cookies(context):
    cookies = [c for c in await context.cookies() if c['domain'].lstrip('.') == 'linkedin.com' or c['domain'].lstrip('.').endswith('.linkedin.com')]
    path = BASE / CONFIG.get('cookies_file', 'cookies.json')
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(cookies, f)
    tmp.chmod(0o600)
    tmp.replace(path)
    LOG.info('Cookies saved count=%s; values omitted', len(cookies))


def load_inputs(base=BASE):
    stage('validate_inputs')
    config = load_config(base)
    limit = config.get('max_messages', 1)
    if type(limit) is not int or limit < 0:
        raise RuntimeError('max_messages must be zero or a positive integer.')
    message = (base / config.get('message_file', 'message.txt')).read_text(encoding='utf-8').strip()
    if not message:
        raise RuntimeError('The message file is empty.')
    cv = base / config.get('cv_file', 'cv.pdf')
    if not cv.is_file() or not cv.read_bytes().startswith(b'%PDF-'):
        raise RuntimeError('The configured CV file is missing or is not a PDF.')
    if '$' in message:
        raise RuntimeError('The message must contain final text without template variables.')
    LOG.info("Inputs validated max_messages=%s message_chars=%s cv_bytes=%s", limit, len(message), cv.stat().st_size)
    return config, limit, message, cv


def recipient_id(href):
    parts = urlsplit(urljoin(URL, href))
    if parts.hostname != 'www.linkedin.com' or parts.path != '/messaging/compose/':
        raise RuntimeError('The message URL is not a supported LinkedIn compose URL.')
    query = parse_qs(parts.query)
    result = query.get('recipient', query.get('profileUrn', []))
    if len(result) != 1:
        raise RuntimeError('The recipient identifier is missing or ambiguous.')
    return result[0].removeprefix('urn:li:fsd_profile:')


def save(journal):
    target = BASE / CONFIG.get('history_file', 'send-history.json')
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)
    report = BASE / CONFIG.get('sent_list_file', 'sent-recipients.txt')
    report.write_text('\n'.join(f"{record.get('name', ident)} | {record['status']} | {record.get('timestamp', record.get('date', ''))} | {ident}" for ident, record in journal.items()) + '\n', encoding='utf-8')
    LOG.debug('Journal saved records=%s', len(journal))


async def open_page(page, url):
    from playwright.async_api import TimeoutError as BrowserTimeout
    attempts = CONFIG.get('navigation_attempts', 3)
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=CONFIG.get('navigation_timeout_ms', 30000))
            return
        except BrowserTimeout:
            LOG.warning('NAVIGATION_TIMEOUT attempt=%s/%s path=%s', attempt, attempts, urlsplit(url).path)
            if '/checkpoint/' in page.url or '/challenge/' in page.url:
                raise RuntimeError('LinkedIn security verification requires manual action.')
            if attempt == attempts:
                raise
            await asyncio.sleep(CONFIG.get('navigation_retry_wait_seconds', 3))


async def prepare_conversation(page, recipient, expect):
    from playwright.async_api import TimeoutError as BrowserTimeout
    # This retry boundary ends before any message text or attachment is submitted.
    attempts = CONFIG.get('conversation_ready_attempts', 3)
    for attempt in range(1, attempts + 1):
        try:
            await open_page(page, recipient['url'])
            if '/checkpoint/' in page.url or '/login' in page.url:
                raise RuntimeError('LinkedIn login or security verification is required.')
            editor = ConnectionsPage(page).message_editor()
            await expect(editor).to_have_count(1)
            await verify_recipient(page, recipient, expect)
            return editor
        except (BrowserTimeout, AssertionError):
            LOG.warning('CONVERSATION_NOT_READY recipient=%s attempt=%s/%s; no message entered', recipient['id'], attempt, attempts)
            if attempt == attempts:
                raise
            await asyncio.sleep(CONFIG.get('navigation_retry_wait_seconds', 3))


async def verify_recipient(page, recipient, expect):
    # LinkedIn has two valid layouts: a selected-recipient chip or a profile link.
    messaging_page = ConnectionsPage(page)
    selected = messaging_page.recipient_chip(recipient['name'])
    if await selected.count() == 1:
        await expect(selected).to_be_visible(timeout=CONFIG.get('browser_timeout_ms', 30000))
        if recipient_id(page.url) != recipient['id']:
            raise RuntimeError('The open conversation does not match the expected recipient.')
        LOG.debug('Recipient verified by compose URL and selected chip id=%s', recipient['id'])
        return
    profile = messaging_page.recipient_profile(recipient['id'])
    await expect(profile).to_be_visible(timeout=CONFIG.get('browser_timeout_ms', 30000))
    LOG.debug('Recipient verified by profile ID link id=%s', recipient['id'])


async def deliver(page, recipient, message, cv, history, expect):
    stage('open_conversation', recipient=recipient['name'], recipient_id=recipient['id'])
    editor = await prepare_conversation(page, recipient, expect)
    stage('fill_message', characters=len(message))
    await editor.fill(message)
    messaging_page = ConnectionsPage(page)
    attachment = messaging_page.attachment_button()
    await expect(attachment).to_have_count(1)
    stage('attach_cv', filename=cv.name, bytes=cv.stat().st_size)
    async with page.expect_file_chooser() as chooser:
        await attachment.click()
    file_chooser = await chooser.value
    await file_chooser.set_files(str(cv))
    await expect(messaging_page.attachment_label(cv.name)).to_be_visible(timeout=CONFIG.get('upload_timeout_ms', 60000))
    stage('wait_send_ready')
    send = messaging_page.send_button()
    await expect(send).to_have_count(1)
    await expect(send).to_be_enabled(timeout=CONFIG.get('upload_timeout_ms', 60000))
    record = dict(name=recipient['name'], status='uncertain', timestamp=datetime.now(timezone.utc).isoformat(),
                  message_sha256=hashlib.sha256(message.encode()).hexdigest(), cv_sha256=hashlib.sha256(cv.read_bytes()).hexdigest())
    history[recipient['id']] = record
    save(history)
    stage('click_send', recipient=recipient['name'])
    await send.click()
    stage('verify_sent', recipient=recipient['name'])
    await expect(editor).to_be_empty(timeout=CONFIG.get('confirmation_timeout_ms', 30000))
    sent_text = messaging_page.last_sent_message()
    await expect(sent_text).to_have_text(message, use_inner_text=True, timeout=CONFIG.get('confirmation_timeout_ms', 30000))
    await expect(messaging_page.attachment_label(cv.name)).to_be_visible()
    record['status'] = 'sent'
    LOG.info('SEND_CONFIRMED recipient=%s id=%s', recipient['name'], recipient['id'])
    save(history)



QUEUE_PATH = BASE / CONFIG.get('queue_file', 'contact-queue.json')
BATCH_SIZE = CONFIG.get('batch_size', 50)


def load_queue():
    data = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else {'contacts': {}, 'complete': False}
    for ident, contact in data['contacts'].items():
        if recipient_id(contact['url']) != ident:
            raise RuntimeError('The saved recipient URL is invalid.')
    return data


def save_queue(queue):
    temp = QUEUE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(QUEUE_PATH)
    # A simple URL list is convenient to inspect; JSON is the authoritative queue.
    (BASE / CONFIG.get('url_list_file', 'contact-urls.txt')).write_text('\n'.join(c['url'] for c in queue['contacts'].values()) + '\n', encoding='utf-8')


def pending_contacts(queue, history):
    return [c for ident, c in queue['contacts'].items() if ident not in history]


def finish_collection(queue, observed_count, displayed_total, state):
    queue['complete'] = displayed_total is not None and observed_count == displayed_total
    queue['collection_exhausted'] = True
    queue['observed_count'] = observed_count
    queue['displayed_total'] = displayed_total
    queue['collected_at'] = datetime.now(timezone.utc).isoformat()
    if not queue['complete']:
        warning = f'Collection exhausted: {observed_count} unique contacts loaded; LinkedIn displays {displayed_total}. Sending continues from saved URLs; full coverage is not confirmed.'
        queue['collection_warning'] = warning
        state.setdefault('warnings', []).append(warning)
        LOG.warning(warning)
    else:
        queue.pop('collection_warning', None)
        LOG.info('COLLECTION_COMPLETE total=%s', displayed_total)
    save_queue(queue)


async def collect_batches(page, queue, history, limit, state, changed):
    try:
        if not CONFIG.get('refresh_contacts', False) and (queue.get('complete') or queue.get('collection_exhausted')):
            LOG.info('COLLECTOR cached list reused count=%s complete=%s', len(queue['contacts']), queue.get('complete'))
            if queue.get('collection_warning'):
                state.setdefault('warnings', []).append(queue['collection_warning'])
            return
        links = ConnectionsPage(page).message_links()
        await links.first.wait_for(state='visible')
        stalled, seen = 0, set()
        last_growth = time.monotonic()
        while not state['stop']:
            before = len(seen)
            batch = await links.evaluate_all("els => els.map(e => ({href:e.getAttribute('href'), label:e.getAttribute('aria-label') || e.innerText}))")
            for entry in batch:
                if not entry['href']:
                    continue
                ident = recipient_id(entry['href'])
                name = LINK_NAME.sub('', entry['label']).strip()
                if not name:
                    raise RuntimeError('A recipient name is missing.')
                seen.add(ident)
                queue['contacts'][ident] = dict(id=ident, name=name, url=urljoin(URL, entry['href']))
            # Persist every lazy-loaded page, even before a 50-contact batch is ready.
            save_queue(queue)
            pending = len(pending_contacts(queue, history))
            if pending >= BATCH_SIZE or (limit and pending >= limit - state['sent']):
                changed.set()
            LOG.info('COLLECTOR visible_pass=%s cached=%s pending=%s sent_this_run=%s', len(seen), len(queue['contacts']), pending, state['sent'])
            if limit and pending + state['sent'] >= limit:
                return
            if len(seen) > before:
                last_growth = time.monotonic()
            stalled = stalled + 1 if len(seen) == before else 0
            if stalled >= CONFIG.get('collection_stall_checks', 5) and time.monotonic() - last_growth >= CONFIG.get('collection_idle_timeout_seconds', 30):
                total_match = re.search(r'([\d,\s]+)\s+connections\b', await ConnectionsPage(page).body_text(), re.I)
                total = int(re.sub(r'\D', '', total_match.group(1))) if total_match else None
                finish_collection(queue, len(seen), total, state)
                return
            more = ConnectionsPage(page).show_more_button()
            if await more.count() == 1 and await more.is_visible():
                await more.click()
            else:
                await links.last.scroll_into_view_if_needed()
                await page.mouse.wheel(0, 1500)
            await asyncio.sleep(CONFIG.get('scroll_wait_seconds', 1.5))
    except Exception as exc:
        state['error'] = exc
        state['stop'] = True
        LOG.exception('COLLECTOR_FAILED')
        await browser_diagnostics(page)
    finally:
        state['producer_done'] = True
        changed.set()


async def send_batches(page, queue, history, message, cv, limit, state, changed, expect):
    try:
        while not state['stop']:
            await changed.wait()
            changed.clear()
            if state['stop']:
                return
            batch = pending_contacts(queue, history)[:BATCH_SIZE]
            if limit:
                batch = batch[:max(0, limit - state['sent'])]
            LOG.info('SENDER_BATCH count=%s sent_this_run=%s', len(batch), state['sent'])
            for recipient in batch:
                if state['stop']:
                    return
                await deliver(page, recipient, message, cv, history, expect)
                state['sent'] += 1
                if limit and state['sent'] >= limit:
                    state['stop'] = True
                    return
            if pending_contacts(queue, history):
                changed.set()
            elif state['producer_done']:
                return
    except Exception as exc:
        state['error'] = exc
        state['stop'] = True
        LOG.exception('SENDER_FAILED')
        await browser_diagnostics(page)


async def run_browser(config, limit, message, cv, history):
    from playwright.async_api import async_playwright, expect
    expect.set_options(timeout=CONFIG.get('assertion_timeout_ms', 30000))
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(BASE / CONFIG.get('browser_profile_directory', '.browser-profile')), headless=CONFIG.get('headless', False))
        context.set_default_timeout(CONFIG.get('browser_timeout_ms', 30000))
        context.set_default_navigation_timeout(CONFIG.get('navigation_timeout_ms', 30000))
        LOG.info('Timeouts: elements=%s navigation=%s assertions=%s', CONFIG.get('browser_timeout_ms', 30000), CONFIG.get('navigation_timeout_ms', 30000), CONFIG.get('assertion_timeout_ms', 30000))
        authenticated, page = False, None
        try:
            await restore_cookies(context)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(CONFIG.get('browser_timeout_ms', 30000))
            stage('open_connections')
            await open_page(page, URL)
            if config.get('login_only', False):
                LOG.info('Complete login in the browser, then press Enter. No messages will be sent.')
                await asyncio.to_thread(input)
                await open_page(page, URL)
            elif '/login' in page.url or '/uas/' in page.url:
                stage('login')
                await LoginPage(page, CONFIG.get('browser_timeout_ms', 30000)).sign_in(
                    config.get('email'), config.get('password')
                )
                await open_page(page, URL)
            await ConnectionsPage(page).message_links().first.wait_for(state='visible')
            authenticated = True
            await save_cookies(context)
            if config.get('login_only', False):
                return
            queue = load_queue()
            state = dict(stop=False, producer_done=False, sent=0, error=None)
            changed = asyncio.Event()
            # Any already cached pending URLs can be sent immediately on resume.
            if pending_contacts(queue, history):
                changed.set()
            if config.get('send_messages', False):
                sender_page = await context.new_page()
                sender_page.set_default_timeout(CONFIG.get('browser_timeout_ms', 30000))
                LOG.info('Messaging started with %s cached URLs and limit %s.', len(queue['contacts']), limit)
                await asyncio.gather(
                    collect_batches(page, queue, history, limit, state, changed),
                    send_batches(sender_page, queue, history, message, cv, limit, state, changed, expect),
                )
            else:
                await collect_batches(page, queue, history, limit, state, changed)
                LOG.info('Collected %s URLs without sending.', len(queue['contacts']))
            if state['error']:
                raise state['error']
            LOG.info('Message workflow completed with %s new sends.', state['sent'])
            for warning in state.get('warnings', []):
                LOG.warning('%s', warning)
        except Exception:
            await browser_diagnostics(page)
            if page is not None and any(part in page.url for part in ('/checkpoint/', '/challenge/', '/login')):
                raise RuntimeError('LinkedIn login or security verification requires manual action.') from None
            raise
        finally:
            if authenticated:
                await save_cookies(context)
            await context.close()


def main():
    global CONFIG, URL, QUEUE_PATH, BATCH_SIZE
    CONFIG = load_config(BASE)
    URL = CONFIG['connections_url']
    QUEUE_PATH = BASE / CONFIG['queue_file']
    BATCH_SIZE = CONFIG['batch_size']
    os.umask(0o077)
    config, limit, message, cv = load_inputs()
    if 'OUTREACH_RUN_LIMIT' in os.environ:
        limit = int(os.environ['OUTREACH_RUN_LIMIT'])
        if limit < 0:
            raise RuntimeError('Invalid supervisor send budget.')
    if type(BATCH_SIZE) is not int or BATCH_SIZE < 1:
        raise RuntimeError('batch_size must be a positive integer.')
    for key in ('send_messages', 'login_only', 'check_files_only', 'headless'):
        if type(config.get(key, False)) is not bool:
            raise RuntimeError(f'{key} must be true or false.')
    if config.get('check_files_only', False):
        LOG.info('Input validation completed; limit=%s.', limit)
        return
    lock_handle = (BASE / '.worker.lock').open('a+')
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        raise RuntimeError('Another sender is already running.')
    try:
        path = BASE / CONFIG.get('history_file', 'send-history.json')
        history = json.loads(path.read_text()) if path.exists() else {}
        if any(r['status'] == 'uncertain' for r in history.values()):
            raise RuntimeError('A previous send is uncertain and requires manual review.')
        asyncio.run(run_browser(config, limit, message, cv, history))
    finally:
        lock_handle.close()


def entrypoint():
    global CONFIG
    started = time.monotonic()
    try:
        CONFIG = load_config(BASE)
        setup_logging()
        main()
        LOG.info('RUN_COMPLETE elapsed_seconds=%.2f', time.monotonic() - started)
    except Exception as exc:
        LOG.exception('RUN_FAILED stage=%s elapsed_seconds=%.2f exception=%s', STAGE, time.monotonic() - started, type(exc).__name__)
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        sys.exit(2 if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError)) else 1)


if __name__ == '__main__':
    entrypoint()
