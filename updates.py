# Copyright (c) 2026 Extrusion Therapy
# SPDX-License-Identifier: AGPL-3.0-only
"""Release discovery and small, per-user update preferences; no installation."""

import json
import os
import platform
import re
import tempfile
import threading
import time
import urllib.request

RELEASES_URL = 'https://api.github.com/repos/rdcstout/BumpMeshForFusion/releases/latest'
DOWNLOAD_PREFIX = 'https://github.com/rdcstout/BumpMeshForFusion/releases/download/'
WEEK = 7 * 24 * 60 * 60


def version(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value or '')
    if not match:
        raise ValueError('The release version could not be understood.')
    return tuple(map(int, match.groups()))


def select_release(release, installed, system=None, machine=None):
    current = version(installed)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('No eligible stable release was returned.')
    remote = version(release.get('tag_name'))
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system == 'Darwin' and machine in ('arm64', 'aarch64', 'x86_64'):
        filename = 'BumpMeshForFusion-macOS.pkg'
    elif system == 'Windows' and machine in ('amd64', 'x86_64'):
        filename = 'BumpMeshForFusion-Windows-Setup.exe'
    else:
        raise ValueError('No tested installer is available for this platform.')
    asset = next((a for a in release.get('assets', []) if a.get('name') == filename), None)
    expected = DOWNLOAD_PREFIX + release['tag_name'] + '/' + filename
    if not asset or asset.get('browser_download_url') != expected or asset.get('size', 0) <= 0:
        raise ValueError('The release does not contain a valid installer for this platform.')
    return {'status': 'available' if remote > current else 'current',
            'version': release['tag_name'].lstrip('v'), 'url': expected}


def fetch_release(installed):
    request = urllib.request.Request(RELEASES_URL, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'BumpMeshForFusion/' + installed})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('The update response was too large.')
    return select_release(json.loads(raw), installed)


def settings_path():
    if platform.system() == 'Windows':
        parent = os.environ.get('APPDATA', os.path.expanduser('~'))
    else:
        parent = os.path.expanduser('~/Library/Application Support')
    return os.path.join(parent, 'BumpMeshForFusion', 'updates.json')


class UpdateChecker:
    def __init__(self, installed, notify, path=None, fetch=fetch_release, clock=time.time):
        self.installed, self.notify = installed, notify
        self.path = path or settings_path()
        self.fetch, self.clock = fetch, clock
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.manual = False
        self.thread = None
        self.state = {'automatic': True, 'last_attempt': 0, 'dismissed': ''}
        try:
            with open(self.path, encoding='utf-8') as handle:
                saved = json.load(handle)
            if isinstance(saved.get('automatic'), bool):
                self.state['automatic'] = saved['automatic']
            stamp = saved.get('last_attempt', 0)
            if isinstance(stamp, (int, float)) and 0 <= stamp <= self.clock():
                self.state['last_attempt'] = stamp
            if isinstance(saved.get('dismissed'), str):
                self.state['dismissed'] = saved['dismissed']
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self):
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.updates-', dir=parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(self.state, handle)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def configure(self, automatic, dismissed=None):
        with self.lock:
            old = self.state.copy()
            self.state['automatic'] = automatic
            if dismissed is not None:
                self.state['dismissed'] = dismissed
            try:
                self._save()
            except OSError:
                self.state = old
                raise
        self.wake.set()

    def check(self, manual=False):
        with self.lock:
            now = self.clock()
            due = now - self.state['last_attempt'] >= WEEK
            if not manual and (not self.state['automatic'] or not due):
                return None
            # Record the attempt, including failures, so offline use cannot retry rapidly.
            self.state['last_attempt'] = now
            try:
                self._save()
            except OSError:
                return {'status': 'error', 'message': 'Could not save update preferences.'} if manual else None
        try:
            result = self.fetch(self.installed)
        except Exception:
            result = {'status': 'error', 'message': 'Could not check for updates. Check your connection and try again.'}
        with self.lock:
            if not manual and (not self.state['automatic'] or result['status'] != 'available'
                               or result['version'] == self.state['dismissed']):
                return None
        return result

    def request(self):
        with self.lock:
            self.manual = True
        self.wake.set()

    def start(self):
        self.thread = threading.Thread(target=self._run, name='BumpMeshUpdates', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            with self.lock:
                manual, self.manual = self.manual, False
            result = self.check(manual)
            if result and not self.stopped.is_set():
                self.notify(result, manual)
            self.wake.wait(3600)

    def stop(self):
        self.stopped.set()
        self.wake.set()
