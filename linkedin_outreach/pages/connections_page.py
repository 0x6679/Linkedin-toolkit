"""LinkedIn connections and messaging page object."""
import re


class ConnectionsPage:
    MESSAGE_LINK_NAME = re.compile(r"^Send a message to ")

    def __init__(self, page):
        self.page = page

    def message_links(self):
        return self.page.get_by_role("link", name=self.MESSAGE_LINK_NAME)

    def message_editor(self):
        return self.page.locator(
            '[contenteditable="true"][role="textbox"]:visible, '
            '[contenteditable="true"][aria-label="Write a message..."]:visible'
        )

    def attachment_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^Attach a file"))

    def send_button(self):
        return self.page.get_by_role("button", name="Send", exact=True)

    def last_sent_message(self):
        return self.page.locator("p.msg-s-event-listitem__body").last

    def recipient_chip(self, name):
        return self.page.get_by_role("button", name=f"Remove {name}", exact=True)

    def recipient_profile(self, identifier):
        return self.page.locator(f'a[href*="/in/{identifier}"]:visible').first

    def attachment_label(self, filename):
        return self.page.get_by_text(filename, exact=True).first

    def show_more_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"^Show more results$|^Show more$", re.I)
        )

    async def body_text(self):
        return await self.page.locator("body").inner_text()

    async def diagnostic_counts(self):
        return {
            "editors": await self.message_editor().count(),
            "sends": await self.send_button().count(),
            "attachments": await self.attachment_button().count(),
        }
