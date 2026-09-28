"""LinkedIn people-search page object."""
import re


class PeopleSearchPage:
    ACTION_TEXT = re.compile(r"^\s*(Connect|Message|Pending|Follow)\s*$", re.I)
    DEGREE_TEXT = re.compile(r"\u2022\s*(1st|2nd|3rd)\b", re.I)

    def __init__(self, page, timeout_ms):
        self.page = page
        self.timeout_ms = timeout_ms

    async def wait_until_ready(self):
        await self.page.locator('main a[href*="/in/"]').first.wait_for(
            state="visible", timeout=self.timeout_ms
        )
        await self.page.locator("main span:visible").filter(
            has_text=self.ACTION_TEXT
        ).first.wait_for(state="visible", timeout=self.timeout_ms)

    async def profile_snapshot(self):
        return await self.page.locator('main a[href*="/in/"]').evaluate_all(
            "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText || ''}))"
        )

    def result_card(self, href):
        escaped = href.replace('"', '\\"')
        link = self.page.locator(f'main a[href="{escaped}"]').first
        return link.locator("xpath=ancestor::*[@role='listitem'][1]")

    @staticmethod
    def connect_action(card):
        return card.get_by_role(
            "link", name=re.compile(r"^(Connect|Invite .+ to connect)$", re.I)
        )

    @staticmethod
    def message_action(card):
        name = re.compile(r"^Message(?: .+)?$", re.I)
        return card.get_by_role("button", name=name).or_(card.get_by_role("link", name=name))

    @staticmethod
    def pending_label(card):
        return card.get_by_text("Pending", exact=True)

    def invitation_dialog(self):
        return self.page.get_by_role("dialog").filter(
            has_text=re.compile(r"Add a note to your invitation", re.I)
        ).first

    @staticmethod
    def add_note_button(dialog):
        return dialog.get_by_role("button", name=re.compile(r"^Add a note$", re.I))

    @staticmethod
    def note_editor(dialog):
        return dialog.locator("textarea")

    @staticmethod
    def send_invitation_button(dialog):
        return dialog.get_by_role(
            "button",
            name=re.compile(r"^(Send(?: without a note)?|Send invitation)$", re.I),
        )

    def next_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^Next$", re.I))
