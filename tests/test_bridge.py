# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
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


if __name__ == '__main__':
    unittest.main()
