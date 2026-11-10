"""Consent, data minimization and real-signup conversion safeguards."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from app import accounts, db
from app.config import config
from app.ui import layout
from app.views_preorder import notify_submit
from app.security import form_timestamp
from tests.support import StoreTestCase


class MetaTrackingTests(StoreTestCase):
    def test_measurement_markup_is_limited_to_homepage(self):
        home = layout(self.make_request(path="/"), "", chrome=False)
        self.assertIn('/static/js/meta.js', home)
        self.assertIn('data-meta-consent hidden', home)
        for route in ('/privacy', '/admin', '/account', '/checkout', '/contact', '/shop'):
            markup = layout(self.make_request(path=route), "", chrome=False)
            self.assertNotIn('/static/js/meta.js', markup)
            self.assertNotIn('data-meta-consent', markup)

    def submit(self, email='signup@example.com', **fields):
        request = self.make_request('POST', '/preorder/notify', form={
            'email': email, 'form_started': form_timestamp(), **fields})
        request.headers['Accept'] = 'application/json'
        return json.loads(notify_submit(request).body)

    def test_only_new_live_signups_receive_a_random_event_id(self):
        with mock.patch.object(config, 'preorder_forms', 'live'):
            first = self.submit()
            duplicate = self.submit()
        self.assertRegex(first['meta_event_id'], r'^[a-f0-9]{32}$')
        self.assertEqual(duplicate['meta_event_id'], '')
        self.assertEqual(db.scalar('SELECT count(*) FROM newsletter'), 1)

    def test_demo_invalid_and_spam_submissions_are_not_conversions(self):
        with mock.patch.object(config, 'preorder_forms', 'demo'):
            self.assertEqual(self.submit()['meta_event_id'], '')
        with mock.patch.object(config, 'preorder_forms', 'live'):
            self.assertFalse(self.submit('invalid')['ok'])
            self.assertEqual(self.submit(company_website='https://spam.example')['meta_event_id'], '')
        self.assertEqual(db.scalar('SELECT count(*) FROM newsletter'), 0)

    def test_rejoining_after_unsubscribing_is_a_new_conversion(self):
        self.assertTrue(accounts.subscribe_newsletter('again@example.com', source='preorder'))
        self.assertFalse(accounts.subscribe_newsletter('again@example.com', source='preorder'))
        db.execute("UPDATE newsletter SET unsubscribed_at = datetime('now')")
        self.assertTrue(accounts.subscribe_newsletter('again@example.com', source='preorder'))

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser-script behavioral tests')
    def test_browser_consent_and_event_behavior(self):
        subprocess.run(['node', str(Path(__file__).with_name('meta_tracking.cjs'))], check=True, capture_output=True)
