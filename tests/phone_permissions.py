"""Opt-in physical all-files revocation check; restores the pre-existing UID grant."""
from pathlib import Path
import subprocess
import sys

serial = sys.argv[1]
adb = str(Path.home()/'Library/Android/sdk/platform-tools/adb')
def shell(*args):
    return subprocess.check_output([adb, '-s', serial, 'shell', *args], text=True)
original = shell('appops', 'get', 'dev.pocketpull', 'MANAGE_EXTERNAL_STORAGE')
if 'Uid mode: MANAGE_EXTERNAL_STORAGE: allow' not in original or 'MANAGE_EXTERNAL_STORAGE: default;' not in original:
    raise RuntimeError('Unexpected existing appops state; refusing to change it')
try:
    shell('appops', 'set', '--uid', 'dev.pocketpull', 'MANAGE_EXTERNAL_STORAGE', 'ignore')
    output = shell('am', 'instrument', '-w', '-e', 'permissionDenied', 'true', 'dev.pocketpull.test/dev.pocketpull.StorageChecks')
    print(output)
    if 'OK (all-files revoked, storage refused, selection cleared)' not in output: raise RuntimeError('Permission validation failed')
finally:
    shell('appops', 'set', '--uid', 'dev.pocketpull', 'MANAGE_EXTERNAL_STORAGE', 'allow')
    restored = shell('appops', 'get', 'dev.pocketpull', 'MANAGE_EXTERNAL_STORAGE')
    if 'Uid mode: MANAGE_EXTERNAL_STORAGE: allow' not in restored: raise RuntimeError('Original UID grant was not restored')
    print('Original all-files grant restored')
Path('build/phone-permissions.txt').write_text(output + '\nOriginal UID allow and package default restored\n')
