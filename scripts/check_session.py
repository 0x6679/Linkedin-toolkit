"""Read-only cookie-session smoke test. Does not collect contacts or send messages."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from linkedin_outreach.config import load_config
from linkedin_outreach.pages.connections_page import ConnectionsPage
from linkedin_outreach.workflows import message as worker


async def main():
    worker.CONFIG = load_config()
    worker.setup_logging()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=worker.CONFIG['headless'])
        try:
            context = await browser.new_context()
            await worker.restore_cookies(context)
            page = await context.new_page()
            await worker.open_page(page, worker.CONFIG['connections_url'])
            if any(part in page.url for part in ('/login', '/checkpoint', '/challenge', '/uas/')):
                raise RuntimeError('Saved session requires login or verification; no files changed.')
            await ConnectionsPage(page).message_links().first.wait_for(
                state='visible', timeout=worker.CONFIG['browser_timeout_ms'])
            print('PASS: saved cookies opened the connections list. No messages sent; session files unchanged.')
        except Exception:
            if 'page' in locals():
                await worker.browser_diagnostics(page)
            worker.LOG.exception('SESSION_CHECK_FAILED')
            raise
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
