import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pocketpull.usb_lock import USBLease
from pocketpull.protocol import TransferCancelled

class USBLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'locks'
        environment = patch.dict(os.environ, {'POCKET_DIMENSION_LOCK_DIR': str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.device = SimpleNamespace(bus=2, address=14)

    def test_serializes_same_device_and_releases(self):
        first = USBLease(self.device); self.addCleanup(first.close)
        with self.assertRaises(TimeoutError): USBLease(self.device, timeout=0)
        first.close()
        second = USBLease(self.device); second.close()

    def test_independent_devices_do_not_block(self):
        first = USBLease(self.device); self.addCleanup(first.close)
        second = USBLease(SimpleNamespace(bus=2, address=15), timeout=0); second.close()

    def test_waiting_lease_can_cancel(self):
        first = USBLease(self.device); self.addCleanup(first.close)
        with self.assertRaises(TransferCancelled): USBLease(self.device, cancelled=lambda: True)

    def test_rejects_symlink_lock(self):
        self.root.mkdir(); target = self.root/'unrelated'; target.write_text('keep')
        (self.root/'2-14.lock').symlink_to(target)
        with self.assertRaises(OSError): USBLease(self.device)
        self.assertEqual(target.read_text(), 'keep')
