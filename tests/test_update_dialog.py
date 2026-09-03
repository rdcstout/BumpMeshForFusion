import json
import os
import tempfile
import types
import unittest

from test_bridge import _load_addin


class UpdateDialogTests(unittest.TestCase):
    def test_result_settings_and_handler_lifetime(self):
        addin = _load_addin()
        with tempfile.TemporaryDirectory() as folder:
            addin._updater = addin.updates.UpdateChecker('0.1.1', lambda *_: None, os.path.join(folder, 'updates.json'))
            values = {}
            def text(key, *_):
                values[key] = types.SimpleNamespace(formattedText='')
                return values[key]
            def boolean(key, _label, _checkbox, _icon, value):
                values[key] = types.SimpleNamespace(value=value)
                return values[key]
            handlers = {}
            command = types.SimpleNamespace(isValid=True,
                commandInputs=types.SimpleNamespace(addTextBoxCommandInput=text, addBoolValueInput=boolean,
                                                    itemById=values.get),
                execute=types.SimpleNamespace(add=lambda h: handlers.update(execute=h)),
                destroy=types.SimpleNamespace(add=lambda h: handlers.update(destroy=h)))
            definition = types.SimpleNamespace(name='Check for Updates')
            addin.adsk.core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(
                userInterface=types.SimpleNamespace(commandDefinitions=types.SimpleNamespace(itemById=lambda _: definition))))
            addin._UpdateCreatedHandler().notify(types.SimpleNamespace(command=command))
            self.assertTrue(addin._updater.manual)
            addin._UpdateResultHandler().notify(types.SimpleNamespace(additionalInfo=json.dumps({
                'result': {'status': 'available', 'version': '0.1.2', 'url': 'https://github.com/test'}, 'manual': True})))
            self.assertIn('Download installer', values['updateStatus'].formattedText)
            self.assertIn('update available', definition.name)
            values['automaticUpdates'].value = False
            # CommandEventArgs need not expose a command property.
            handlers['execute'].notify(types.SimpleNamespace())
            self.assertFalse(addin._updater.state['automatic'])
            self.assertEqual(addin._updater.state['dismissed'], '0.1.2')
            self.assertEqual(definition.name, 'Check for Updates')
            handlers['destroy'].notify(None)
            self.assertIsNone(addin._update_command)
            self.assertEqual(addin._handlers, [])
