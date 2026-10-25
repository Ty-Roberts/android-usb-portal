from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
import json
from unittest.mock import ANY, Mock, patch

from pocketpull import cli
from pocketpull.discovery import candidates, label, device_record, identify_accessory, record_label
from test_usb import device


class DiscoveryTests(unittest.TestCase):
    def test_model_and_stable_phone_ids_distinguish_same_models(self):
        phone = device(0x18d1, 0x2d00)
        phone.product = "PocketPull"; phone.serial_number = "PocketPull-Host"
        first = device_record(phone, {"manufacturer": "samsung", "model": "SM-S918U1", "device_id": "phone-111111111111"})
        second = device_record(phone, {"manufacturer": "samsung", "model": "SM-S918U1", "device_id": "phone-222222222222"})
        self.assertEqual(first['name'], 'Samsung SM-S918U1')
        self.assertIsNone(first['serial'])
        self.assertNotEqual(record_label(first), record_label(second))
        self.assertNotIn('PocketPull-Host', record_label(first))

    def test_pre_accessory_usb_model_and_serial(self):
        phone = device(0x04e8, 0x6860)
        phone.product = 'Galaxy S23 Ultra'; phone.serial_number = 'R5C-example'
        record = device_record(phone)
        self.assertEqual(record_label(record), 'Galaxy S23 Ultra · Serial R5C-example')

    def test_unreadable_descriptors_keep_location_fallback(self):
        class Restricted:
            idVendor=0x04e8; idProduct=0x6860; bus=2; address=5
            @property
            def serial_number(self): raise OSError('Denied')
            @property
            def product(self): raise OSError('Denied')
        record=device_record(Restricted())
        self.assertIsNone(record['serial']); self.assertEqual(record['bus'], 2)
        self.assertIn('Samsung', record['name'])

    def test_busy_accessory_discovery_does_not_open_usb(self):
        with patch('pocketpull.usb_lock.USBLease', side_effect=TimeoutError('busy')), patch('pocketpull.usb_transport.USBTransport') as transport:
            self.assertEqual(identify_accessory(device(0x18d1,0x2d00)), {})
            transport.assert_not_called()

    def test_connect_json_contains_phone_identity_without_selection(self):
        self.assertEqual(self.invoke([device(0x18d1,0x2d00)], ['connect','--json']), 0)
        value=json.loads(self.stdout.getvalue())
        self.assertEqual(value['device']['device_id'],'device-a')
        self.assertFalse(value['selected'])

    def test_common_ids_and_non_phone_same_vendor(self):
        samsung = device(0x04e8, 0x6860)
        pixel = device(0x18d1, 0x4ee1)
        xiaomi = device(0x2717, 0xff48)
        samsung_peripheral = device(0x04e8, 0x1234)
        fastboot = device(0x18d1, 0x4ee0)
        self.assertEqual(candidates([samsung_peripheral, samsung, pixel, xiaomi, fastboot]), [samsung, pixel, xiaomi])
        self.assertIn("Samsung", label(samsung))
        self.assertIsNone(label(samsung_peripheral))

    def invoke(self, found, args):
        self.stdout, self.stderr = StringIO(), StringIO()
        self.transport = Mock()
        self.connection = Mock()
        self.connection.device_id = "device-a"
        self.connection.device_info = {}
        self.connection.selected.return_value = {"selected": False}
        self.connection.pull.return_value = {"bytes": 12, "sha256": "0" * 64}
        self.accessory = device(0x18d1, 0x2d00)
        with patch.object(cli, "devices", return_value=found), \
             patch.object(cli, "switch", return_value=self.accessory) as switch, \
             patch.object(cli, "USBLease"), \
             patch.object(cli, "USBTransport", return_value=self.transport), \
             patch.object(cli, "Connection", return_value=self.connection), \
             redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            result = cli.main(args)
        self.switch = switch
        return result

    def test_no_args_connects_recognized_phone(self):
        phone = device(0x04e8, 0x6860)
        self.assertEqual(self.invoke([device(), phone], []), 0)
        self.switch.assert_called_once_with(phone)
        self.connection.hello.assert_called_once()
        self.assertIn("Choose a file", self.stdout.getvalue())
        self.transport.close.assert_called_once()

    def test_existing_accessory_does_not_switch(self):
        self.assertEqual(self.invoke([device(0x18d1, 0x2d00)], ["connect"]), 0)
        self.switch.assert_not_called()

    def test_status_auto_connects_and_keeps_stdout_json(self):
        self.assertEqual(self.invoke([device(0x18d1, 0x4ee1)], ["status"]), 0)
        self.switch.assert_called_once()
        self.assertEqual(self.stdout.getvalue().strip(), '{\n  "selected": false\n}')

    def test_unknown_phone_not_probed_automatically(self):
        self.assertEqual(self.invoke([device()], ["connect"]), 1)
        self.switch.assert_not_called()
        self.assertIn("devices --all", self.stderr.getvalue())

    def test_multiple_phones_never_choose_arbitrarily(self):
        phones = [device(0x04e8, 0x6860, address=1), device(0x18d1, 0x2d00, address=2)]
        self.assertEqual(self.invoke(phones, ["connect"]), 1)
        self.switch.assert_not_called()
        self.assertIn("--bus N --address N", self.stderr.getvalue())
        self.assertEqual(self.invoke(phones, ["connect", "--bus", "1", "--address", "1"]), 0)
        self.switch.assert_called_once_with(phones[0])

    def test_explicit_unknown_connect_still_supported(self):
        unknown = device()
        self.assertEqual(self.invoke([unknown], ["connect", "--vid", "0x1234", "--pid", "0x5678"]), 0)
        self.switch.assert_called_once_with(unknown)

    def test_device_listing_defaults_to_phones(self):
        found = [device(), device(0x04e8, 0x6860)]
        self.assertEqual(self.invoke(found, ["devices"]), 0)
        self.assertIn("Samsung", self.stdout.getvalue())
        self.assertNotIn("1234:5678", self.stdout.getvalue())
        self.assertEqual(self.invoke(found, ["devices", "--all"]), 0)
        self.assertIn("1234:5678", self.stdout.getvalue())

    def test_gui_launch_does_not_access_usb(self):
        with patch.object(cli.Path, "exists", return_value=True), patch.object(cli.subprocess, "run") as launch, patch.object(cli, "devices") as scan:
            self.assertEqual(cli.main(["gui"]), 0)
            launch.assert_called_once()
            scan.assert_not_called()

    def test_gui_missing_bundle_reports_how_to_build(self):
        with patch.object(cli.Path, "exists", return_value=False), redirect_stderr(StringIO()) as errors:
            self.assertEqual(cli.main(["gui"]), 1)
            self.assertIn("build-gui.sh", errors.getvalue())

    def test_retry_refuses_a_different_phone(self):
        self.assertEqual(self.invoke([device(0x18d1, 0x2d00)], ["pull", "test.bin", "--device-id", "device-b"]), 1)
        self.connection.pull.assert_not_called()
        self.assertIn("different phone", self.stderr.getvalue())

    def test_retry_refuses_a_changed_scope(self):
        self.assertEqual(self.invoke([device(0x18d1, 0x2d00)], ["pull", "test.bin", "--scope-id", "original-scope"]), 1)
        self.connection.pull.assert_not_called()
        self.assertIn("sharing scope changed", self.stderr.getvalue())

    def test_matching_retry_phone_can_transfer(self):
        self.assertEqual(self.invoke([device(0x18d1, 0x2d00)], ["pull", "test.bin", "--device-id", "device-a"]), 0)
        self.connection.pull.assert_called_once()

    def test_pull_uses_automatic_connection(self):
        self.assertEqual(self.invoke([device(0x04e8, 0x6860)], ["pull", "test.bin"]), 0)
        self.switch.assert_called_once()
        self.connection.pull.assert_called_once_with("test.bin", remote="", progress=ANY, conflict="error", resume=False)


if __name__ == "__main__":
    unittest.main()
