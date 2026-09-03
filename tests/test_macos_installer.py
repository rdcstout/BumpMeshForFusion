"""Exercise the postinstall transaction only inside a temporary fake home."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class MacInstallerTests(unittest.TestCase):
    def run_installer(self, fail_promotion=False, prior=True):
        with tempfile.TemporaryDirectory(prefix='bumpmesh-install-test-') as directory:
            root = Path(directory)
            home = root / 'Audit User'
            target = home / 'Library/Application Support/Autodesk/FusionAddins/BumpMeshForFusion'
            if prior:
                target.mkdir(parents=True)
                (target / 'old.txt').write_text('working version')
            payload = root / 'payload' / 'BumpMeshForFusion'
            payload.mkdir(parents=True)
            for name in ['BumpMeshForFusion.py', 'BumpMeshForFusion.manifest']:
                (payload / name).write_text('replacement')
            script = (Path(__file__).resolve().parents[1] / 'packaging/macos/scripts/postinstall').read_text()
            script = script.replace('$(/usr/bin/stat -f \'%Su\' /dev/console)', 'test-user')
            script = script.replace('/usr/bin/dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory',
                                    "printf '%s\\n' " + shlex.quote('NFSHomeDirectory: ' + str(home)))
            script = script.replace('/private/tmp/com.extrusiontherapy.bumpmeshforfusion', str(root / 'payload'))
            script = script.replace('/usr/sbin/chown -R "$CONSOLE_USER":staff "$STAGE/new"', ':')
            if fail_promotion:
                script = script.replace('/bin/mv "$STAGE/new" "$INSTALL_DIR"', 'false')
            result = subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True, timeout=10)
            if fail_promotion:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((target / 'old.txt').read_text(), 'working version')
                self.assertFalse((target / 'BumpMeshForFusion.py').exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((target / 'BumpMeshForFusion.py').read_text(), 'replacement')
                self.assertFalse((target / 'old.txt').exists())
            self.assertEqual(list(target.parent.glob('.bumpmesh-install.*')), [])

    def test_fresh_install_home_with_space(self):
        self.run_installer(prior=False)

    def test_upgrade_home_with_space(self):
        self.run_installer()

    def test_failed_promotion_restores_previous_install(self):
        self.run_installer(fail_promotion=True)
