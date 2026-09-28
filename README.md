# LinkedIn Outreach

Python and Playwright automation for two LinkedIn workflows:

- send a message and PDF attachment to existing connections;
- search for people by keyword and send Connect invitations with an optional note.

The project uses a Page Object Model. LinkedIn selectors and page interactions live in `/pages`, while workflow rules live in `/workflows`.

This is an independent engineering project and is not affiliated with or endorsed by LinkedIn.

## Structure

- Page Object Model keeps selectors separate from workflow decisions.
- Persistent queues and atomic JSON journals allow interrupted runs to resume.
- Actions enter an `uncertain` state before the final UI click, preventing blind retries.
- Bounded retries, process locks, timeouts, and exponential restart delays limit failure loops.
- Cookie restoration and multi-layout login handling reduce repeated manual setup.
- Two-tab concurrency collects lazy-loaded contacts while the sender processes saved work.
- Unit tests cover configuration validation, URL safety, pagination, duplicate prevention, and CLI routing.

## Requirements

- Python 3.10 or newer
- macOS or Linux (`fcntl` is used for process locks)
- an English LinkedIn interface

Install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Create the private configuration and content files:

```bash
cp config.example.json config.json
cp message.example.txt message.txt
cp notes.example.txt notes.txt
```

Place the resume at `cv.pdf`, then edit `config.json`. The example configuration contains no credentials. Private data, browser sessions, histories, queues, PDFs, messages, notes, and logs are excluded by `.gitignore`.

## Commands

```bash
python3 start.py message
python3 start.py add-contact
```

The positional command is required. No workflow script needs to be started directly.

## Message workflow

The workflow opens the connections page, collects lazy-loaded compose URLs, and sends from a second tab while collection continues. It attaches the configured PDF and records each recipient before clicking Send.

- `max_messages`: maximum messages for one command run; `0` means every pending recipient.
- `send_messages`: when `false`, collect URLs without sending.
- `batch_size`: number of cached recipients released to the sender at once.
- `refresh_contacts`: rescan LinkedIn instead of reusing an exhausted saved queue.

`send-history.json` prevents duplicates. A record becomes `uncertain` before Send and `sent` only after UI confirmation. An uncertain record blocks automatic restart until it is reviewed.

## Add-contact workflow

The workflow converts `people_search_keyword` into a properly encoded LinkedIn people-search URL, follows numbered pages, waits for the action controls to render, and acts only on the visual Connect action. Enter only a keyword such as `it recruiter`, `qa engineer`, or `automation tester`; no URL is required. It records Message results as already connected, Pending results as pending, and other results as unavailable.

- `max_connection_requests`: maximum invitations for one command run; `0` means every eligible result.
- `max_search_pages`: maximum numbered pages; `0` means continue until Next is unavailable.
- `use_connection_note`: enable optional notes.
- `notes_file`: note text, limited to 200 characters.

When LinkedIn offers **Add a note** and the configured note exists, it is attached. Otherwise the invitation is sent without a note. `connection-history.json` prevents duplicate invitations, and `known-contacts.json` stores observed statuses.

## Login and recovery

Both workflows restore `cookies.json` and the persistent browser profile. If the session expires, the shared `LoginPage` supports combined, two-step, and remembered-email password screens using the private credentials in `config.json`. LinkedIn security verification still requires manual action.

`start.py` runs each workflow under the crash supervisor. Failed workers restart with an increasing delay, within the configured retry limit. Automatic restart stops when an action is uncertain.


## Page objects and workflows

`LoginPage` detects login and verification states and signs in across LinkedIn login layouts. `ConnectionsPage` owns connection-list, editor, attachment, Send, and sent-message locators. `PeopleSearchPage` waits for rendered actions, snapshots profiles, locates real result cards, exposes Connect/Message/Pending actions, and identifies the invitation dialog.

The message workflow manages lazy loading, two-tab concurrency, attachment verification, and send journaling. The contact workflow manages pagination, 200-character note validation, known-contact classification, and invitation journaling. The supervisor selects the workflow, carries the remaining run budget across restarts, and prevents concurrent duplicate runs.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_session.py
```

Unit tests do not open LinkedIn or send anything. The session check opens the connections page without modifying queues or sending messages.

## Responsible use

Use conservative limits, review message content, and comply with LinkedIn's terms and applicable privacy and anti-spam rules. UI automation depends on external page structure and can require selector updates. Start with limits of one and inspect the journals and logs before increasing them.

Contact me at: https://www.linkedin.com/in/farkas-laszlo-18a27a156/
or: laszl0.farkas at protonmail.ch
