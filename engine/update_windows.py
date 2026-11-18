"""Update now, on Windows only: the steps after engine/update.py has found a newer version and the person pressed Update now.

  1. The installer named in app-v1.json is downloaded through engine/setup/download.py (downloads.lastbook.org only, port 443),
     into this computer's Lastbook folder (%LOCALAPPDATA%\\Lastbook\\updates). Its size and SHA-256 must match app-v1.json.
  2. Windows checks its Authenticode signature: it must be valid, and signed by the same publisher as the Lastbook.exe that is
     running. A copy of Lastbook that is not signed (a build from source) never installs an update; it points to the website.
  3. Only when the person then presses Restart and update: the installer starts with /update and this app closes. The installer
     waits for Lastbook to close, installs over it for this user (LastbookData and settings stay), and opens Lastbook again.

Nothing here runs on its own: every step starts from a button. engine/update.py holds the state and the routes.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

INSTALLER = re.compile(r'^Lastbook-(\d{1,6}(?:\.\d{1,6}){0,3})-Setup\.exe$')
APP_EXE = 'Lastbook.exe'
DETACHED, NEW_GROUP, BREAKAWAY, NO_WINDOW = 0x00000008, 0x00000200, 0x01000000, 0x08000000


def system_root():
    return Path(os.environ.get('SystemRoot', r'C:\Windows'))


POWERSHELL = system_root() / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


def install_dir():
    """Where Lastbook-<version>-Setup.exe installs (packaging/lastbook_setup.py, install_dir)."""
    local = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(local) / 'Programs' / 'Lastbook'


def updates_dir():
    local = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(local) / 'Lastbook' / 'updates'


def running_exe():
    """This app's Lastbook.exe, or None outside a built app."""
    return Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None


def installed_copy():
    """True when this app is the copy the installer put in place: only that copy can be updated in place."""
    me = running_exe()
    try:
        return me is not None and me.name.lower() == APP_EXE.lower() and me.parent == install_dir().resolve()
    except OSError:
        return False


def clear_old(keep=None):
    """Removes earlier downloads from the updates folder, except keep (a file name and its .part files)."""
    d = updates_dir()
    if not d.is_dir():
        return
    for p in d.iterdir():
        if keep and p.name.startswith(keep):
            continue
        if p.is_file() and p.name.startswith('Lastbook-'):
            try:
                p.unlink()
            except OSError:
                pass


# Paths travel as environment variables, so no path is ever quoted into code. Exit codes are read by check_signature.
SIGNATURE = r'''$ErrorActionPreference = 'Stop'
$new = Get-AuthenticodeSignature -LiteralPath $env:LB_NEW
$me = Get-AuthenticodeSignature -LiteralPath $env:LB_SELF
if ($me.Status -ne 'Valid') { exit 11 }
if ($new.Status -ne 'Valid') { exit 12 }
if ($new.SignerCertificate.Subject -cne $me.SignerCertificate.Subject) { exit 13 }
exit 0'''

SIGNATURE_ERRORS = {
    11: 'This copy of Lastbook is not signed, so it cannot check an update. Download the new version from lastbook.org.',
    12: 'The download is not signed properly, so Lastbook did not keep it. Try again, or download it from lastbook.org.',
    13: 'The download is signed by someone other than Lastbook, so Lastbook did not keep it. Download it from lastbook.org.',
}


def check_signature(path):
    """None if Windows says path is validly signed by the publisher of this Lastbook.exe, else a message for the person."""
    me = running_exe()
    if me is None:
        return SIGNATURE_ERRORS[11]
    env = dict(os.environ, LB_NEW=str(path), LB_SELF=str(me))
    try:
        r = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command', SIGNATURE], env=env, capture_output=True,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, cwd=str(system_root()), timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return 'Windows could not check the download\'s signature. Try again, or download it from lastbook.org.'
    if r.returncode == 0:
        return None
    return SIGNATURE_ERRORS.get(r.returncode, 'Windows could not check the download\'s signature. Try again, or download it from lastbook.org.')


def launch(installer):
    """Starts the installer with /update, apart from this app, so it outlives it. Raises OSError if Windows would not start it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('_PYI', '_MEI'))}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'      # the installer is a PyInstaller program too: it must not take this one's settings
    last = None
    for flags in (DETACHED | NEW_GROUP | BREAKAWAY, DETACHED | NEW_GROUP):     # BREAKAWAY fails inside a job that forbids it
        try:
            subprocess.Popen([str(installer), '/update'], cwd=str(system_root()), env=env, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError as e:
            last = e
    raise last
