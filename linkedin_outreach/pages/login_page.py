"""LinkedIn login page object."""
import asyncio
import re


class LoginPage:
    def __init__(self, page, timeout_ms):
        self.page = page
        self.timeout_ms = timeout_ms

    @property
    def requires_login(self):
        return "/login" in self.page.url or "/uas/" in self.page.url

    @property
    def requires_verification(self):
        return "/checkpoint" in self.page.url or "/challenge" in self.page.url

    async def sign_in(self, email, password):
        if not email or not password:
            raise RuntimeError("The saved session expired and login credentials are empty.")
        sign_in_link = self.page.get_by_role("link", name=re.compile(r"^Sign in$", re.I))
        if await sign_in_link.count() and await sign_in_link.first.is_visible():
            await sign_in_link.first.click()
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
        username_submitted = False
        while asyncio.get_running_loop().time() < deadline:
            for frame in self.page.frames:
                username = frame.locator(
                    '#username, input[name="session_key"], input[autocomplete="username"], input[type="email"]'
                ).first
                password_field = frame.locator(
                    '#password, input[name="session_password"], input[autocomplete="current-password"], input[type="password"]'
                ).first
                username_visible = bool(await username.count()) and await username.is_visible()
                password_visible = bool(await password_field.count()) and await password_field.is_visible()
                if password_visible:
                    if username_visible:
                        await username.fill(email)
                    await password_field.fill(password)
                    submit = frame.locator('button[type="submit"]').first
                    if not await submit.count():
                        submit = frame.get_by_role(
                            "button", name=re.compile(r"^(Sign in|Continue)$", re.I)
                        ).first
                    await submit.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    return
                if username_visible and not username_submitted:
                    await username.fill(email)
                    submit = frame.locator('button[type="submit"]').first
                    if await submit.count() and await submit.is_visible():
                        await submit.click()
                        username_submitted = True
                        await asyncio.sleep(0.5)
                        break
            await asyncio.sleep(0.5)
        raise RuntimeError("LinkedIn did not expose a usable password field.")
