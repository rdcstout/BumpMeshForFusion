# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request


def _load_addin():
    adsk = types.ModuleType('adsk')
    core = types.ModuleType('adsk.core')
    fusion = types.ModuleType('adsk.fusion')

    class _Handler:
        def __init__(self):
            pass

    core.CommandEventHandler = _Handler
    core.CommandCreatedEventHandler = _Handler
    core.HTMLEventHandler = _Handler
    core.CustomEventHandler = _Handler
    fusion.BRepBody = type('BRepBody', (), {'cast': staticmethod(lambda value: value)})
    fusion.BRepFace = type('BRepFace', (), {'cast': staticmethod(lambda _value: None)})

    adsk.core = core
    adsk.fusion = fusion
    sys.modules['adsk'] = adsk
    sys.modules['adsk.core'] = core
    sys.modules['adsk.fusion'] = fusion

    root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    path = os.path.join(root, 'BumpMeshForFusion.py')
    specification = importlib.util.spec_from_file_location('bumpmesh_for_fusion', path, submodule_search_locations=[root])
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.addin = _load_addin()
        self.directory = tempfile.mkdtemp(prefix='bumpmesh-bridge-test-')
        self.source = os.path.join(self.directory, 'TestBody.stl')
        with open(self.source, 'wb') as source_file:
            source_file.write(b'fusion-source-stl')
        self.addin._server_token = 'test-token'
        self.texture_directory = os.path.join(self.directory, 'persistent-textures')
        self.addin._user_texture_root = lambda: self.texture_directory
        self.addin._jobs['job-1'] = {
            'directory': self.directory,
            'source': self.source,
            'outputs': {},
        }
        self.addin._server = self.addin.http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0),
            self.addin._BridgeHandler,
        )
        self.thread = self.addin.threading.Thread(
            target=self.addin._server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base = 'http://127.0.0.1:{}'.format(
            self.addin._server.server_address[1]
        )

    def tearDown(self):
        self.addin._server.shutdown()
        self.addin._server.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_source_requires_token_and_output_round_trips(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(
                self.base + '/api/jobs/job-1/source',
                timeout=3,
            )
        self.assertEqual(denied.exception.code, 403)

        source_response = urllib.request.urlopen(
            self.base + '/api/jobs/job-1/source?token=test-token',
            timeout=3,
        )
        self.assertEqual(source_response.read(), b'fusion-source-stl')

        request = urllib.request.Request(
            self.base + '/api/jobs/job-1/output?token=test-token&filename=Textured.stl',
            data=b'textured-output-stl',
            method='POST',
        )
        response = urllib.request.urlopen(request, timeout=3)
        self.assertEqual(response.status, 200)

        output = next(iter(self.addin._jobs['job-1']['outputs'].values()))
        with open(output['path'], 'rb') as output_file:
            self.assertEqual(output_file.read(), b'textured-output-stl')
        self.assertEqual(output['filename'], 'Textured.stl')

    def _save_texture(self, name='Leather.png', data=None, content_type='image/png'):
        if data is None:
            data = b'\x89PNG\r\n\x1a\n' + b'custom-map-data'
        request = urllib.request.Request(
            self.base + '/api/textures?token=test-token&filename=' + urllib.parse.quote(name),
            data=data,
            headers={'Content-Type': content_type},
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_saved_textures_persist_list_load_and_delete(self):
        status, payload = self._save_texture('Leather grain.jpg')
        self.assertEqual(status, 201)
        self.assertEqual(payload['texture']['id'], 'Leather_grain.png')

        with urllib.request.urlopen(
            self.base + '/api/textures?token=test-token', timeout=3
        ) as response:
            listed = json.loads(response.read())['textures']
        self.assertEqual([item['id'] for item in listed], ['Leather_grain.png'])

        with urllib.request.urlopen(
            self.base + '/api/textures/Leather_grain.png?token=test-token', timeout=3
        ) as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertEqual(response.read(), b'\x89PNG\r\n\x1a\ncustom-map-data')

        # The files are independent of the ephemeral HTTP server and job directory.
        self.addin._server.shutdown()
        self.addin._server.server_close()
        self.thread.join(timeout=2)
        self.addin._server = self.addin.http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0), self.addin._BridgeHandler
        )
        self.thread = self.addin.threading.Thread(
            target=self.addin._server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = 'http://127.0.0.1:{}'.format(self.addin._server.server_address[1])
        with urllib.request.urlopen(
            self.base + '/api/textures?token=test-token', timeout=3
        ) as response:
            self.assertEqual(json.loads(response.read())['textures'][0]['id'], 'Leather_grain.png')

        delete = urllib.request.Request(
            self.base + '/api/textures/Leather_grain.png?token=test-token', method='DELETE'
        )
        with urllib.request.urlopen(delete, timeout=3) as response:
            self.assertEqual(json.loads(response.read())['status'], 'deleted')
        self.assertFalse(os.path.exists(os.path.join(self.texture_directory, 'Leather_grain.png')))

    def test_duplicate_texture_names_do_not_overwrite(self):
        _, first = self._save_texture('Dots.png')
        _, second = self._save_texture('Dots.png', b'\x89PNG\r\n\x1a\nsecond')
        self.assertEqual(first['texture']['id'], 'Dots.png')
        self.assertEqual(second['texture']['id'], 'Dots-2.png')
        with open(os.path.join(self.texture_directory, 'Dots.png'), 'rb') as handle:
            self.assertEqual(handle.read(), b'\x89PNG\r\n\x1a\ncustom-map-data')

    def test_texture_routes_reject_unauthorized_invalid_and_traversal_requests(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.base + '/api/textures', timeout=3)
        self.assertEqual(denied.exception.code, 403)

        with self.assertRaises(urllib.error.HTTPError) as wrong_type:
            self._save_texture('Map.png', content_type='image/jpeg')
        self.assertEqual(wrong_type.exception.code, 415)

        with self.assertRaises(urllib.error.HTTPError) as invalid_png:
            self._save_texture('Map.png', data=b'not-a-png')
        self.assertEqual(invalid_png.exception.code, 400)

        with self.assertRaises(urllib.error.HTTPError) as traversal:
            urllib.request.urlopen(
                self.base + '/api/textures/%2E%2E%2Fsecret.png?token=test-token', timeout=3
            )
        self.assertEqual(traversal.exception.code, 404)

    def test_texture_size_limit_is_enforced_before_writing(self):
        self.addin.MAX_TEXTURE_BYTES = 8
        with self.assertRaises(urllib.error.HTTPError) as oversized:
            self._save_texture(data=b'\x89PNG\r\n\x1a\nextra')
        self.assertEqual(oversized.exception.code, 413)
        self.assertFalse(os.path.exists(self.texture_directory))

    def test_failed_texture_promotion_leaves_no_partial_map(self):
        original_replace = self.addin.os.replace
        def fail_replace(_source, _destination):
            raise OSError('simulated promotion failure')
        self.addin.os.replace = fail_replace
        try:
            with self.assertRaises(urllib.error.HTTPError) as failed:
                self._save_texture('KeepClean.png')
            self.assertEqual(failed.exception.code, 500)
            self.assertEqual(os.listdir(self.texture_directory), [])
        finally:
            self.addin.os.replace = original_replace


if __name__ == '__main__':
    unittest.main()
