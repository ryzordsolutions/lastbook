"""Check for updates, in the desktop app: only when the person asks (About's button, or Lastbook > Check for Updates… on a Mac).
Nothing here runs at startup or on a timer. On the Mac and Linux the check only says whether a newer version is on the website.
On Windows, the installed app can also update itself, one button at a time (engine/update_windows.py): Update now downloads the
installer and checks it, and Restart and update runs it.

  - One small GET of METADATA through engine/setup/download.py, the one way out (HTTPS, no proxy). Only https://downloads.lastbook.org
    on port 443 is accepted, also after a redirect: not the bucket's r2.dev address, not Hugging Face.
  - METADATA (app-v1.json at the bucket root): {"version": 1, "mac": {"version", "min_os"}, "windows": {...}}. On the Mac and Linux
    it is display information only, and other fields (url, size, sha256, notes) are not read.
  - The "windows" entry may also name its installer: "url" (https://downloads.lastbook.org/Lastbook-<version>-Setup.exe, the same
    version), "size" and "sha256". Without all three, or with any of them odd, Windows is told to use the website, as before.
    The download must match size and SHA-256, then carry a valid Authenticode signature from the publisher of the running
    Lastbook.exe. Only the copy the installer put in %LOCALAPPDATA%\\Programs\\Lastbook offers Update now.

Routes, under /api/update/: GET state; POST check; and on Windows POST install, cancel, restart.
"""
import json
import os
import platform
import plistlib
import re
import sys
import threading
import traceback
import urllib.parse
from pathlib import Path

from engine.setup import download

METADATA = 'https://downloads.lastbook.org/app-v1.json'
HOST = 'downloads.lastbook.org'
VER = re.compile(r'^\d{1,6}(\.\d{1,6}){0,3}$')    # Windows builds have 5 digits (10.0.26200)
MAX_BODY = 4096
MAX_INSTALLER = 2 << 30                            # bytes: far more than the installer, far less than a disk
SHA256 = re.compile(r'^[0-9a-f]{64}$')
WEBSITE = 'lastbook.org'

OFFLINE = 'Lastbook could not reach the internet. Everything else in Lastbook still works without it; try again when you are online.'


def _ver(s):
    return tuple(int(x) for x in s.split('.')) if isinstance(s, str) and VER.match(s) else None


def platform_key():
    return 'mac' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32' else 'linux' if sys.platform.startswith('linux') else None


def _mac_bundle():
    if not getattr(sys, 'frozen', False):
        return None
    for b in Path(sys.executable).resolve().parents:
        if b.suffix == '.app':
            return b
    return None


def _windows_exe_version(path):
    import ctypes
    from ctypes import wintypes as wt
    v = ctypes.windll.version
    n = v.GetFileVersionInfoSizeW(str(path), None)
    if not n:
        return None
    buf = ctypes.create_string_buffer(n)
    if not v.GetFileVersionInfoW(str(path), 0, n, buf):
        return None
    p, ln = ctypes.c_void_p(), wt.UINT()
    if not v.VerQueryValueW(buf, '\\', ctypes.byref(p), ctypes.byref(ln)) or ln.value < 52:
        return None
    f = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32 * 13)).contents      # VS_FIXEDFILEINFO: [2] FileVersionMS, [3] FileVersionLS
    return f'{f[2] >> 16}.{f[2] & 0xFFFF}.{f[3] >> 16}'


def app_version():
    """This app's version as 'X.Y.Z', or None outside a built app (LASTBOOK_VERSION stands in for tests)."""
    try:
        if sys.platform == 'darwin' and _mac_bundle():
            with open(_mac_bundle() / 'Contents' / 'Info.plist', 'rb') as f:
                return str(plistlib.load(f).get('CFBundleShortVersionString') or '') or None
        if sys.platform == 'win32' and getattr(sys, 'frozen', False):
            return _windows_exe_version(sys.executable)
        if sys.platform.startswith('linux') and getattr(sys, 'frozen', False):    # the .deb's /opt/lastbook/VERSION
            v = (Path(sys._MEIPASS) / 'VERSION').read_text(encoding='utf-8').strip()
            return v if VER.match(v) else None
    except Exception:
        traceback.print_exc()
    return os.environ.get('LASTBOOK_VERSION') or None


def os_version():
    if sys.platform == 'darwin':
        return platform.mac_ver()[0] or '0'
    if sys.platform == 'win32':
        w = sys.getwindowsversion()
        return f'{w.major}.{w.minor}.{w.build}'
    if sys.platform.startswith('linux'):     # Ubuntu's 24.04, from os-release
        try:
            return platform.freedesktop_os_release().get('VERSION_ID') or '0'
        except OSError:
            return '0'
    return '0'


def validate(m, key, allowed=None):
    """This platform's version and minimum system from app-v1.json, or None if they are not there or not plain version numbers.
    On Windows, also its installer (see installer()), or None in 'installer' when there is none to trust."""
    if not isinstance(m, dict) or m.get('version') != 1 or not isinstance(m.get(key), dict):
        return None
    e = m[key]
    if not _ver(e.get('version')):
        return None
    out = {'version': e['version'], 'min_os': e['min_os'] if _ver(e.get('min_os')) else None}
    if key == 'windows':
        out['installer'] = installer(e, allowed)
    return out


def installer(e, allowed=None):
    """{'url', 'name', 'size', 'sha256'} of the Windows installer in an app-v1.json entry, or None unless every part is as expected:
    https on HOST (port 443; a test passes its own hosts in allowed, on any port), one file named Lastbook-<this version>-Setup.exe,
    nothing after it, a whole number of bytes up to MAX_INSTALLER, and a lowercase SHA-256."""
    from engine import update_windows as win
    url, size, sha = e.get('url'), e.get('size'), e.get('sha256')
    if not isinstance(url, str) or type(size) is not int or not 0 < size <= MAX_INSTALLER or not isinstance(sha, str) or not SHA256.match(sha):
        return None
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return None
    hosts = {HOST} if allowed is None else allowed
    if u.scheme != 'https' or u.username or u.password or u.query or u.fragment or (u.hostname or '') not in hosts:
        return None
    if allowed is None and port not in (None, 443):
        return None
    name = u.path[1:] if u.path.startswith('/') else ''
    m = win.INSTALLER.match(name)
    if not m or m.group(1) != e['version']:
        return None
    return {'url': url, 'name': name, 'size': size, 'sha256': sha}


OS_NAMES = {'mac': 'macOS', 'windows': 'Windows', 'linux': 'Ubuntu'}


def _has_no_entry(data, key):
    try:
        m = json.loads(data)
    except ValueError:
        return False
    return isinstance(m, dict) and m.get('version') == 1 and key not in m


class Updater:
    """allowed and context are for tests (a local HTTPS server); the product passes neither and reaches only HOST on port 443."""

    def __init__(self, url=METADATA, allowed=None, context=None, key=None, version=None, win=None):
        self.url, self.context = url, context
        self.product = allowed is None
        self.allowed = {HOST} if allowed is None else allowed
        self.key = key or platform_key()
        self.version = version or app_version()
        self.lock = threading.Lock()
        self.s = {'state': 'idle'}
        self.thread = None
        self.offer = None          # the last 'available' offer, with its installer on Windows
        self.control = None        # the installer's download, while it runs
        self.ready = None          # the checked installer, once Restart and update can run it
        self.win = win             # engine/update_windows.py; tests pass a stand-in

    def state(self):
        with self.lock:
            out = dict(self.s)
        out['current'] = self.version
        out['website'] = WEBSITE
        return out

    def _set(self, **kw):
        with self.lock:
            self.s = kw

    def _windows(self):
        if self.win is None:
            from engine import update_windows
            self.win = update_windows
        return self.win

    def can_install(self, offer):
        """Update now is offered only on Windows, with a trusted installer entry, in the copy the installer put in place."""
        return self.key == 'windows' and bool(offer and offer.get('installer')) and self._windows().installed_copy()

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def check(self):
        if not self.key:
            return {'error': 'Updates are for the Mac, Windows and Linux app.', 'code': 'unsupported'}
        if self.busy() or self.s.get('state') in ('ready', 'restarting'):
            return self.state()
        self._set(state='checking')
        self.thread = threading.Thread(target=self._guard, daemon=True, name='update-check')
        self.thread.start()
        return self.state()

    def _guard(self):
        try:
            self._check()
        except Exception as e:
            traceback.print_exc()
            self._set(state='error', code='internal', message=f'The check stopped on an internal error ({type(e).__name__}).')

    def _fetch(self):
        """(status, bytes) of the metadata through download.py. Raises download.Failed with code 'offline' when nothing answers."""
        if self.product and urllib.parse.urlsplit(self.url).port not in (None, 443):
            raise download.Failed('network', 'Lastbook refused to check an address it does not know.')
        try:
            conn, r, final = download._open(self.url, 0, None, self.allowed, self.context or download.tls_context(), 15)
        except download.Failed:
            raise
        except download.ssl.SSLCertVerificationError:
            raise download.Failed('network', 'Lastbook could not confirm it was talking to the real download site, so it stopped. '
                                             'A network that intercepts secure connections (some offices, hotels) can cause this.')
        except (OSError, ValueError, download.http.client.HTTPException):   # names through download.py: no network module here
            raise download.Failed('offline', OFFLINE)
        try:
            if self.product and urllib.parse.urlsplit(final).port not in (None, 443):     # a redirect on HOST to another port
                return 0, b''
            data = r.read(MAX_BODY + 1) if r.status == 200 else b''
            return r.status, data
        except OSError:
            raise download.Failed('offline', OFFLINE)
        finally:
            conn.close()

    def _check(self):
        try:
            status, data = self._fetch()
        except download.Failed as f:
            return self._set(state='error', code=f.code, message=f.message)
        if status in (403, 404):
            return self._set(state='not_published', message='No update information yet. Try again later.')
        if status != 200 or len(data) > MAX_BODY:
            return self._set(state='error', code='server', message='The download site did not answer properly. Try again later.')
        try:
            offer = validate(json.loads(data), self.key, None if self.product else self.allowed)
        except ValueError:
            offer = None
        if not offer and self.key == 'linux' and _has_no_entry(data, self.key):   # Linux is announced only once the site lists it
            return self._set(state='not_published', message='No update information yet. Try again later.')
        if not offer:
            return self._set(state='error', code='bad_info', message='The update information on the download site could not be read. Try again later.')
        mine = _ver(self.version or '')
        if mine and _ver(offer['version']) <= mine:
            return self._set(state='up_to_date', latest=offer['version'])
        if offer['min_os'] and _ver(os_version()) and _ver(os_version()) < _ver(offer['min_os']):
            return self._set(state='too_old_os', version=offer['version'],
                             message=f"Version {offer['version']} needs {OS_NAMES.get(self.key, self.key)} {offer['min_os']} or later.")
        self.offer = offer
        self._available()

    def _available(self):
        extra = {'can_install': self.can_install(self.offer)} if self.key == 'windows' else {}
        self._set(state='available', version=self.offer['version'], **extra)

    # ---- Update now (Windows): each step starts from a button

    def install(self):
        """Update now: downloads the installer named in app-v1.json and checks it. The state goes downloading, verifying, ready."""
        if self.key != 'windows':
            return {'error': 'Download the new version from lastbook.org.', 'code': 'unsupported'}
        if self.busy() or self.s.get('state') in ('ready', 'restarting'):
            return self.state()
        if self.s.get('state') != 'available' or not self.can_install(self.offer):
            return {'error': 'There is no update to install here. Check for updates first, or download it from lastbook.org.', 'code': 'not_available'}
        self.control = download.Control()
        self._set(state='downloading', version=self.offer['version'], done=0, total=self.offer['installer']['size'])
        self.thread = threading.Thread(target=self._install_guard, daemon=True, name='update-install')
        self.thread.start()
        return self.state()

    def _install_guard(self):
        try:
            self._install()
        except Exception as e:
            traceback.print_exc()
            self._set(state='error', code='internal', message=f'The update stopped on an internal error ({type(e).__name__}).')

    def _install(self):
        win, inst, version = self._windows(), self.offer['installer'], self.offer['version']
        win.updates_dir().mkdir(parents=True, exist_ok=True)
        win.clear_old(keep=inst['name'])
        dest = win.updates_dir() / inst['name']
        dest.unlink(missing_ok=True)      # a finished file is downloaded again: only a partial one (.part) is resumed, and re-hashed

        def progress(done, total, phase):
            if phase == 'downloading' and self.s.get('state') == 'downloading':
                self._set(state='downloading', version=version, done=done, total=total)

        d = download.Download(inst['url'], dest, inst['size'], inst['sha256'], self.control, progress,
                              allowed=self.allowed, context=self.context or download.tls_context())
        try:
            d.run()
        except download.Stopped:
            d.discard()
            return self._available()
        except download.Failed as f:
            return self._set(state='error', code=f.code, message=f.message)
        self._set(state='verifying', version=version)
        problem = win.check_signature(dest)
        if problem:
            dest.unlink(missing_ok=True)
            return self._set(state='error', code='signature', message=problem)
        self.ready = dest
        self._set(state='ready', version=version)

    def cancel(self):
        if self.control is not None and self.s.get('state') in ('downloading', 'verifying'):
            self.control.stop('cancelled')
        return self.state()

    def restart(self):
        """Restart and update: starts the checked installer with /update, then closes Lastbook so it can be replaced."""
        if self.key != 'windows' or self.s.get('state') != 'ready' or self.ready is None:
            return {'error': 'There is no checked update to install. Press Update now first.', 'code': 'not_ready'}
        try:
            self._windows().launch(self.ready)
        except OSError as e:
            self._set(state='error', code='launch', message=f'Windows would not start the installer ({e.strerror or type(e).__name__}). Download it from lastbook.org.')
            return self.state()
        self._set(state='restarting', version=self.offer['version'])
        threading.Timer(1.0, quit_app).start()     # time for this answer to reach the page first
        return self.state()


QUIT = None        # the shell's way to close its window (app/shell_windows.py sets it); without one, the process ends


def quit_app():
    if QUIT is not None:
        try:
            return QUIT()
        except Exception:
            traceback.print_exc()
    os._exit(0)


UPDATER = None


def updater():
    global UPDATER
    if UPDATER is None:
        UPDATER = Updater()
    return UPDATER


ROUTES = {
    ('GET', 'state'): lambda u: u.state(),
    ('POST', 'check'): lambda u: u.check(),
    ('POST', 'install'): lambda u: u.install(),
    ('POST', 'cancel'): lambda u: u.cancel(),
    ('POST', 'restart'): lambda u: u.restart(),
}


def handle(h, method, path):
    """Answers one /api/update/ request on a BaseHTTPRequestHandler h. This computer only."""
    from engine.setup.api import _send_json
    if h.client_address[0] not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        return _send_json(h, {'error': 'Updates are only open on this computer.'}, 403)
    fn = ROUTES.get(('GET' if method == 'HEAD' else method, path[len('/api/update/'):].strip('/')))
    if not fn:
        return _send_json(h, {'error': 'not found'}, 404)
    if method == 'POST':
        try:
            n = int(h.headers.get('Content-Length') or 0)
            if 0 < n <= 4096:
                h.rfile.read(n)
        except ValueError:
            pass
    try:
        out = fn(updater())
    except Exception as e:
        traceback.print_exc()
        return _send_json(h, {'error': f'The check stopped on an internal error ({type(e).__name__}).'}, 500)
    return _send_json(h, out)
