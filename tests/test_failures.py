import json
import os
import socket
import tempfile
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import test_bridge
from test_bridge import _load_addin


class TransferFailures(test_bridge.BridgeTests):
    def test_invalid_unicode_token_is_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + '/api/jobs/job-1/source?token=%E9%9B%B6')
        self.assertEqual(error.exception.code, 403)

    def test_oversized_upload_is_rejected(self):
        with patch.object(self.addin, 'MAX_UPLOAD_BYTES', 2):
            request = urllib.request.Request(self.base + '/api/jobs/job-1/output?token=test-token&filename=x.stl', data=b'abc')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 413)

    def test_unicode_source(self):
        renamed = os.path.join(self.directory, '零件.stl')
        os.rename(self.source, renamed)
        self.addin._jobs['job-1']['source'] = renamed
        with urllib.request.urlopen(self.base + '/api/jobs/job-1/source?token=test-token') as result:
            self.assertEqual(result.read(), b'fusion-source-stl')

    def test_truncated_upload_is_removed(self):
        connection = socket.create_connection(self.addin._server.server_address, timeout=3)
        with connection:
            connection.sendall(b'POST /api/jobs/job-1/output?token=test-token&filename=x.stl HTTP/1.0\r\nContent-Length: 10\r\n\r\nabc')
            connection.shutdown(socket.SHUT_WR)
            while connection.recv(4096):
                pass
        self.assertEqual(os.listdir(self.directory), ['TestBody.stl'])
        self.assertEqual(self.addin._jobs['job-1']['outputs'], {})

    def test_bad_size_format_and_job(self):
        for suffix, data, status in [('filename=x.exe', b'abc', 400), ('filename=x.stl', b'', 413)]:
            request = urllib.request.Request(self.base + '/api/jobs/job-1/output?token=test-token&' + suffix, data=data)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, status)

    def test_active_job_survives_retirement_until_released(self):
        with self.addin._job_access('job-1') as job:
            self.addin._clean_retired_jobs(keep='new-job')
            self.assertTrue(os.path.isdir(job['directory']))
            with self.addin._job_access('job-1') as retired:
                self.assertIsNone(retired)
        self.assertNotIn('job-1', self.addin._jobs)
        self.assertFalse(os.path.exists(self.directory))


class SaveFailures(unittest.TestCase):
    def setUp(self):
        self.addin = _load_addin()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = os.path.join(self.directory.name, 'output.stl')
        with open(self.source, 'wb') as handle:
            handle.write(b'new texture')
        self.destination = os.path.join(self.directory.name, 'saved.stl')
        self.dialog = types.SimpleNamespace(filename=self.destination, showSave=lambda: 1)
        self.addin.adsk.core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(
            userInterface=types.SimpleNamespace(createFileDialog=lambda: self.dialog)))
        self.addin.adsk.core.DialogResults = types.SimpleNamespace(DialogOK=1)
        self.addin._jobs['job'] = {'directory': self.directory.name, 'outputs': {
            'output': {'path': self.source, 'filename': 'texture.stl'}}}

    def save(self):
        return self.addin._save_output({'jobId': 'job', 'outputId': 'output'})

    def test_saved_and_staging_removed(self):
        self.assertEqual(self.save()['status'], 'saved')
        with open(self.destination, 'rb') as handle:
            self.assertEqual(handle.read(), b'new texture')
        self.assertFalse(os.path.exists(self.source))
        self.assertEqual(self.addin._jobs['job']['outputs'], {})

    def test_3mf_filter_and_extension(self):
        self.addin._jobs['job']['outputs']['output']['filename'] = 'texture.3mf'
        self.dialog.filename = os.path.join(self.directory.name, 'saved')
        result = self.save()
        self.assertTrue(result['path'].endswith('.3mf'))
        self.assertEqual(self.dialog.filter, '3MF files (*.3mf)')

    def test_failed_replace_preserves_existing_file(self):
        with open(self.destination, 'wb') as handle:
            handle.write(b'previous texture')
        with patch.object(self.addin.os, 'replace', side_effect=OSError('destination locked')):
            with self.assertRaises(OSError):
                self.save()
        with open(self.destination, 'rb') as handle:
            self.assertEqual(handle.read(), b'previous texture')
        self.assertEqual(os.listdir(self.directory.name), ['saved.stl'])

    def test_cancel_does_not_write_destination(self):
        self.dialog.showSave = lambda: 0
        self.assertEqual(self.save()['status'], 'cancelled')
        self.assertFalse(os.path.exists(self.destination))
        self.assertFalse(os.path.exists(self.source))

    def test_failed_copy_preserves_existing_destination(self):
        with open(self.destination, 'wb') as handle:
            handle.write(b'previous texture')
        with patch.object(self.addin.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.save()
        with open(self.destination, 'rb') as handle:
            self.assertEqual(handle.read(), b'previous texture')
        self.assertEqual(os.listdir(self.directory.name), ['saved.stl'])

    def test_failed_model_export_removes_job_directory(self):
        manager = types.SimpleNamespace(createSTLExportOptions=lambda *_: None)
        self.addin.adsk.core.Application.get = lambda: types.SimpleNamespace(activeProduct=object())
        self.addin.adsk.fusion.Design = types.SimpleNamespace(cast=lambda _: types.SimpleNamespace(exportManager=manager))
        try:
            with self.assertRaises(RuntimeError):
                self.addin._export_body(types.SimpleNamespace(name='Body'))
            self.assertEqual(os.listdir(self.addin._temporary_directory.name), [])
        finally:
            if self.addin._temporary_directory:
                self.addin._temporary_directory.cleanup()
