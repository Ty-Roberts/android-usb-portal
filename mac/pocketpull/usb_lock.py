"""Serialize CLI clients sharing one approved USB accessory interface."""
import fcntl
import os
from pathlib import Path
import stat
import time


class USBLease:
    def __init__(self, device, timeout=15, cancelled=lambda: False):
        root = Path(os.environ.get('POCKET_DIMENSION_LOCK_DIR', str(Path.home() / 'Library/Caches/PocketDimension/usb-locks')))
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or root.stat().st_uid != os.getuid(): raise OSError('Unsafe USB lock directory')
        self.descriptor = os.open(root / f'{device.bus}-{device.address}.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        info = os.fstat(self.descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            self.close(); raise OSError('Unsafe USB lock file')
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled():
                    from .protocol import TransferCancelled
                    raise TransferCancelled('Cancelled while waiting for USB')
                try: fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() >= deadline: raise TimeoutError('Phone busy with another Pocket Dimension transfer; retry shortly')
                    time.sleep(0.05)
        except BaseException:
            self.close(); raise

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor); self.descriptor = None
