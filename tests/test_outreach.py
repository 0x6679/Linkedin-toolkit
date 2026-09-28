import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import start
from linkedin_outreach.config import load_config
from linkedin_outreach.supervisor import ready_to_restart
from linkedin_outreach.workflows import contacts
from linkedin_outreach.workflows import message as worker


class OutreachTests(unittest.TestCase):
    def test_config_defaults_disable_sending(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'config.json').write_text('{}')
            c = load_config(p)
            self.assertFalse(c['send_messages'])
            self.assertEqual(c['max_messages'], 1)
            self.assertEqual(c['password'], '')

    def test_config_rejects_bad_limits(self):
        for value in (-1, True, '0'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                p = Path(d)
                (p/'config.json').write_text(json.dumps({'max_messages': value}))
                with self.assertRaises(ValueError):
                    load_config(p)

    def test_recipient_validation(self):
        self.assertEqual(worker.recipient_id('/messaging/compose/?recipient=urn:li:fsd_profile:abc'), 'abc')
        for url in ('https://example.com/messaging/compose/?recipient=a', '/messaging/compose/?recipient=a&recipient=b', '/in/a'):
            with self.assertRaises(RuntimeError):
                worker.recipient_id(url)

    def test_people_search_pagination(self):
        url = contacts.page_url('it recruiter', 2)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(query['keywords'], ['it recruiter'])
        self.assertEqual(query['origin'], ['CLUSTER_EXPANSION'])
        with self.assertRaises(ValueError):
            contacts.page_url('', 1)
        with self.assertRaises(ValueError):
            contacts.page_url('qa engineer', 0)

    def test_note_rules(self):
        with tempfile.TemporaryDirectory() as d, patch.object(contacts, 'BASE', Path(d)):
            config = {'use_connection_note': True, 'notes_file': 'notes.txt'}
            self.assertIsNone(contacts.load_note(config))
            (Path(d)/'notes.txt').write_text('x' * 201)
            with self.assertRaises(ValueError):
                contacts.load_note(config)

    def test_sent_and_uncertain_contacts_are_not_retried(self):
        queue = {'contacts': {k: {'id': k} for k in ('sent', 'uncertain', 'new')}}
        history = {'sent': {'status': 'sent'}, 'uncertain': {'status': 'uncertain'}}
        self.assertEqual(worker.pending_contacts(queue, history), [{'id': 'new'}])
        with patch('linkedin_outreach.supervisor.read_history', return_value=history):
            self.assertFalse(ready_to_restart({}, 'message'))

    def test_cli_commands(self):
        self.assertEqual(start.parse_args(['message']).command, 'message')
        self.assertEqual(start.parse_args(['add-contact']).command, 'add-contact')

    def test_queue_roundtrip_and_identity_check(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            q = {'contacts': {'abc': {'id': 'abc', 'name': 'Example', 'url': 'https://www.linkedin.com/messaging/compose/?recipient=abc'}}, 'complete': False}
            with patch.object(worker, 'BASE', base), patch.object(worker, 'QUEUE_PATH', base/'queue.json'):
                worker.save_queue(q)
                self.assertEqual(worker.load_queue(), q)
                q['contacts']['wrong'] = q['contacts'].pop('abc')
                worker.save_queue(q)
                with self.assertRaises(RuntimeError):
                    worker.load_queue()

if __name__ == '__main__':
    unittest.main()
