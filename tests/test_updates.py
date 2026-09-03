import json
import os
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import _load_addin

updates = _load_addin().updates


def release(tag='v0.1.2', name='BumpMeshForFusion-macOS.pkg', **extra):
    return dict(tag_name=tag, assets=[{'name': name, 'size': 42,
        'browser_download_url': updates.DOWNLOAD_PREFIX + tag + '/' + name}], **extra)


class ReleaseTests(unittest.TestCase):
    def select(self, value, installed='0.1.1', system='Darwin', machine='arm64'):
        return updates.select_release(value, installed, system, machine)

    def test_numeric_comparison(self):
        for remote, local, expected in [('v0.1.2', '0.1.1', 'available'),
                                        ('v0.1.1', '0.1.1', 'current'),
                                        ('v0.1.0', '0.1.1', 'current'),
                                        ('v1.10.0', '1.9.0', 'available')]:
            self.assertEqual(self.select(release(remote), local)['status'], expected)

    def test_ineligible_and_malformed(self):
        for value in [release(prerelease=True), release(draft=True), release('v0.1.2-beta'),
                      release('nonsense'), release('v01.1.2'), {'tag_name': 'v0.1.2'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.select(value)
        with self.assertRaises(ValueError):
            self.select(release(), installed='unknown')

    def test_platform_and_download_origin(self):
        self.select(release(name='BumpMeshForFusion-Windows-Setup.exe'), system='Windows', machine='AMD64')
        for system, machine in [('Linux', 'x86_64'), ('Windows', 'ARM64')]:
            with self.assertRaises(ValueError):
                self.select(release(), system=system, machine=machine)
        value = release()
        value['assets'][0]['browser_download_url'] = 'https://example.com/installer.pkg'
        with self.assertRaises(ValueError):
            self.select(value)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'updates.json')
        self.now = updates.WEEK * 10
        self.calls = []
        self.result = {'status': 'available', 'version': '0.1.2', 'url': 'test'}

    def checker(self, fetch=None):
        def request(installed):
            self.calls.append(installed)
            return self.result.copy()
        return updates.UpdateChecker('0.1.1', lambda *_: None, self.path,
                                     fetch=fetch or request, clock=lambda: self.now)

    def test_weekly_persisted_across_restart(self):
        self.assertEqual(self.checker().check()['status'], 'available')
        self.assertIsNone(self.checker().check())
        self.now += updates.WEEK - 1
        self.assertIsNone(self.checker().check())
        self.now += 1
        self.assertEqual(self.checker().check()['status'], 'available')
        self.assertEqual(len(self.calls), 2)

    def test_disabled_and_dismissed_do_not_block_manual(self):
        checker = self.checker()
        checker.configure(False, '0.1.2')
        self.assertIsNone(self.checker().check())
        self.assertEqual(self.checker().check(True)['status'], 'available')
        checker.configure(True, '0.1.2')
        self.now += updates.WEEK
        self.assertIsNone(self.checker().check())
        self.result['version'] = '0.1.3'
        self.now += updates.WEEK
        self.assertEqual(self.checker().check()['version'], '0.1.3')

    def test_failures_are_quiet_bounded_and_manual_is_explicit(self):
        def offline(_):
            raise OSError('offline')
        self.assertIsNone(self.checker(offline).check())
        self.assertIsNone(self.checker().check())
        self.assertEqual(self.checker(offline).check(True)['status'], 'error')

    def test_corrupt_settings_and_future_clock(self):
        with open(self.path, 'w') as handle:
            handle.write('invalid')
        self.assertEqual(self.checker().check()['status'], 'available')
        with open(self.path, 'w') as handle:
            json.dump({'last_attempt': self.now + updates.WEEK}, handle)
        self.assertEqual(self.checker().state['last_attempt'], 0)

    def test_current_release_does_not_notify_automatically(self):
        self.result = {'status': 'current', 'version': '0.1.1'}
        self.assertIsNone(self.checker().check())
        self.assertEqual(self.checker().check(True)['status'], 'current')

    def test_preferences_write_failure_does_not_claim_success(self):
        checker = self.checker()
        with patch.object(checker, '_save', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                checker.configure(False)
            self.assertTrue(checker.state['automatic'])
            self.assertEqual(checker.check(True)['status'], 'error')
        self.assertEqual(self.calls, [])

    def test_manual_request_runs_in_worker_and_stops(self):
        import threading
        event = threading.Event()
        results = []
        checker = self.checker()
        checker.configure(False)
        checker.notify = lambda result, manual: (results.append((result, manual)), event.set())
        checker.start()
        try:
            checker.request()
            self.assertTrue(event.wait(2))
            self.assertTrue(results[0][1])
            self.assertEqual(results[0][0]['status'], 'available')
        finally:
            checker.stop()
            checker.thread.join(2)
        self.assertFalse(checker.thread.is_alive())
