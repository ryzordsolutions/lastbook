"""Check for updates (engine/update.py) against a local HTTPS test server (test_setup.py's). Never shipped.
On the Mac and Linux the check only says what is on the website: these tests make sure nothing else can be asked of it there.
On Windows, Update now downloads and checks the installer: these tests run it with a stand-in for engine/update_windows.py
(no signature check or program start happens here), and make sure it trusts only what it should.

Run:  python3 app/tests/test_update.py
"""
import hashlib
import json
import sys
import time
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1])]
import test_setup as T            # noqa: E402
from engine import update         # noqa: E402

ALLOWED = {'127.0.0.1'}


def wait(u, t=20):
    end = time.time() + t
    while time.time() < end:
        s = u.state()
        if s['state'] not in ('checking', 'downloading', 'verifying'):
            return s
        time.sleep(0.05)
    raise AssertionError(f'still {u.state()}')


class FakeWindows:
    """Stands in for engine/update_windows.py: no PowerShell, no program started."""
    from engine.update_windows import INSTALLER

    def __init__(self, folder, installed=True, signature=None):
        self.folder, self.installed, self.signature = Path(folder), installed, signature
        self.checked, self.launched = [], []

    def installed_copy(self):
        return self.installed

    def updates_dir(self):
        return self.folder

    def clear_old(self, keep=None):
        for p in self.folder.glob('Lastbook-*'):
            if not (keep and p.name.startswith(keep)):
                p.unlink()

    def check_signature(self, path):
        self.checked.append(Path(path).read_bytes())
        return self.signature

    def launch(self, path):
        self.launched.append(Path(path))


class UpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.files = T.Files()
        cls.srv, cls.ctx, cls.base = T.start_server(cls.tmp, cls.files)

    def meta(self, version='1.2.0', min_os='10.0', **extra):
        self.files.data['app-v1.json'] = json.dumps({'version': 1, 'mac': dict(version=version, min_os=min_os, **extra),
                                                     'windows': dict(version=version, min_os=min_os, **extra)}).encode()

    def check(self, version='1.1.2', url=None):
        u = update.Updater(url or f'{self.base}/app-v1.json', allowed=ALLOWED, context=self.ctx, key='mac', version=version)
        u.check()
        return wait(u)

    def test_available(self):
        self.meta(url='https://evil.example/x.dmg', sha256='0' * 64, size=1)    # anything else in the file is not read
        s = self.check()
        self.assertEqual((s['state'], s['version'], s['current'], s['website']), ('available', '1.2.0', '1.1.2', 'lastbook.org'))
        self.assertEqual(set(s), {'state', 'version', 'current', 'website'})

    def test_up_to_date(self):
        self.meta(version='1.1.2')
        self.assertEqual(self.check()['state'], 'up_to_date')

    def test_offline(self):
        s = self.check(url='https://127.0.0.1:1/app-v1.json')     # an allowed address that nothing answers
        self.assertEqual(s['code'], 'offline')
        self.assertIn('still works', s['message'])

    def test_not_published(self):
        self.files.data.pop('app-v1.json', None)
        self.assertEqual(self.check()['state'], 'not_published')

    def test_bad_version(self):
        self.meta(version='<b>2</b>')
        self.assertEqual(self.check()['code'], 'bad_info')

    def test_too_old_os(self):
        self.meta(min_os='99.0')
        self.assertEqual(self.check()['state'], 'too_old_os')

    def test_windows_build_numbers(self):
        self.assertEqual(update._ver('10.0.26200'), (10, 0, 26200))     # five-digit Windows builds must compare
        self.assertGreater(update._ver('10.0.26200'), update._ver('10.0'))

    def test_product_reaches_only_downloads_lastbook_org(self):
        u = update.Updater(key='mac', version='1.0.0')
        self.assertEqual((u.allowed, u.url), ({'downloads.lastbook.org'}, 'https://downloads.lastbook.org/app-v1.json'))
        for other in ('https://pub-28c3cc06eb774c5cac057e6c9343efbd.r2.dev/app-v1.json', 'https://huggingface.co/app-v1.json',
                      'https://downloads.lastbook.org:8443/app-v1.json'):
            u = update.Updater(key='mac', version='1.0.0')
            u.url = other
            u.check()
            s = wait(u)
            self.assertEqual(s['state'], 'error', other)
            self.assertNotEqual(s['state'], 'available')

    def test_routes(self):
        self.assertEqual(set(update.ROUTES), {('GET', 'state'), ('POST', 'check'), ('POST', 'install'), ('POST', 'cancel'), ('POST', 'restart')})
        for gone in ('download', '_install_mac'):
            self.assertFalse(hasattr(update.Updater, gone), gone)
        self.assertNotIn('subprocess', vars(update))      # only engine/update_windows.py starts a program

    def test_mac_and_linux_never_install(self):
        for key in ('mac', 'linux'):
            self.meta(**self.setup_entry(b'x' * 10))
            u = update.Updater(f'{self.base}/app-v1.json', allowed=ALLOWED, context=self.ctx, key=key, version='1.1.2',
                               win=FakeWindows(self.tmp))
            u.check()
            s = wait(u)
            self.assertNotIn('can_install', s)
            self.assertEqual(u.install()['code'], 'unsupported')
            self.assertEqual(u.restart()['code'], 'not_ready')

    # ---- Windows: Update now

    def setup_entry(self, body, version='1.2.0', name=None):
        name = name or f'Lastbook-{version}-Setup.exe'
        self.files.data[name] = body
        return dict(url=f'{self.base}/{name}', size=len(body), sha256=hashlib.sha256(body).hexdigest())

    def win_check(self, entry, version='1.1.2', **fake):
        self.files.data['app-v1.json'] = json.dumps({'version': 1, 'windows': dict(version='1.2.0', min_os='10.0', **entry)}).encode()
        folder = Path(tempfile.mkdtemp(dir=self.tmp))
        win = FakeWindows(folder, **fake)
        u = update.Updater(f'{self.base}/app-v1.json', allowed=ALLOWED, context=self.ctx, key='windows', version=version, win=win)
        u.check()
        return u, win, wait(u)

    def test_windows_update_now(self):
        body = b'MZ installer ' * 5000
        u, win, s = self.win_check(self.setup_entry(body))
        self.assertEqual((s['state'], s['can_install']), ('available', True))
        self.assertEqual(u.restart()['code'], 'not_ready')            # nothing to run before Update now
        u.install()
        s = wait(u)
        self.assertEqual(s['state'], 'ready', s)
        self.assertEqual(win.checked, [body])                         # the signature is checked on the downloaded file
        self.assertEqual(win.launched, [])                            # nothing runs until Restart and update
        quits = []
        update.QUIT = lambda: quits.append(1)
        try:
            self.assertEqual(u.restart()['state'], 'restarting')
            time.sleep(1.3)
        finally:
            update.QUIT = None
        self.assertEqual(win.launched, [win.folder / 'Lastbook-1.2.0-Setup.exe'])
        self.assertEqual(win.launched[0].read_bytes(), body)
        self.assertEqual(quits, [1])

    def test_windows_bad_checksum_is_not_kept(self):
        entry = self.setup_entry(b'MZ real' * 100)
        entry['sha256'] = hashlib.sha256(b'something else').hexdigest()
        u, win, _ = self.win_check(entry)
        u.install()
        s = wait(u)
        self.assertEqual(s['state'], 'error')
        self.assertEqual(win.checked, [])
        self.assertFalse(any(win.folder.iterdir()))

    def test_windows_bad_signature_is_not_kept(self):
        u, win, _ = self.win_check(self.setup_entry(b'MZ unsigned' * 100), signature='not signed')
        u.install()
        s = wait(u)
        self.assertEqual((s['state'], s['code'], s['message']), ('error', 'signature', 'not signed'))
        self.assertFalse((win.folder / 'Lastbook-1.2.0-Setup.exe').exists())
        self.assertEqual(u.restart()['code'], 'not_ready')

    def test_windows_cancel(self):
        self.files.pace = 0.05
        try:
            u, win, _ = self.win_check(self.setup_entry(b'MZ' * 400_000))
            u.install()
            time.sleep(0.3)
            u.cancel()
            s = wait(u)
        finally:
            self.files.pace = 0
        self.assertEqual((s['state'], s['can_install']), ('available', True))
        self.assertFalse(any(win.folder.iterdir()))

    def test_windows_not_installed_copy_uses_website(self):
        u, win, s = self.win_check(self.setup_entry(b'MZ' * 10), installed=False)
        self.assertEqual((s['state'], s['can_install']), ('available', False))
        self.assertEqual(u.install()['code'], 'not_available')

    def test_windows_installer_entry_must_be_exact(self):
        good = self.setup_entry(b'MZ' * 10)
        bad = [
            dict(good, url=good['url'].replace('1.2.0', '1.1.9')),                 # another version than the entry's
            dict(good, url=good['url'].replace('Setup.exe', 'Setup.msi')),
            dict(good, url=good['url'] + '?x=1'),
            dict(good, url=good['url'].replace('/Lastbook-', '/sub/Lastbook-')),
            dict(good, url=good['url'].replace('https:', 'http:')),
            dict(good, url=good['url'].replace('127.0.0.1', 'localhost')),          # a host not allowed
            dict(good, sha256=good['sha256'].upper()),
            dict(good, size=0), dict(good, size=True), dict(good, size=str(good['size'])), dict(good, size=3 << 30),
            {k: v for k, v in good.items() if k != 'sha256'},
        ]
        for entry in bad:
            u, win, s = self.win_check(entry)
            self.assertEqual((s['state'], s['can_install']), ('available', False), entry)
            self.assertEqual(u.install()['code'], 'not_available')

    def test_product_installer_only_from_downloads_lastbook_org(self):
        e = {'version': '1.2.0', 'size': 10, 'sha256': '0' * 64}
        ok = update.installer(dict(e, url='https://downloads.lastbook.org/Lastbook-1.2.0-Setup.exe'))
        self.assertEqual(ok['name'], 'Lastbook-1.2.0-Setup.exe')
        for url in ('https://downloads.lastbook.org:8443/Lastbook-1.2.0-Setup.exe', 'https://huggingface.co/Lastbook-1.2.0-Setup.exe',
                    'https://pub-28c3cc06eb774c5cac057e6c9343efbd.r2.dev/Lastbook-1.2.0-Setup.exe',
                    'https://user@downloads.lastbook.org/Lastbook-1.2.0-Setup.exe'):
            self.assertIsNone(update.installer(dict(e, url=url)), url)


if __name__ == '__main__':
    unittest.main(verbosity=2)
