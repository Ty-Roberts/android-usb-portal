from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pocketpull import usb_transport as usb
from pocketpull.protocol import Connection
from pocketpull.cli import main


def device(vid=0x1234, pid=0x5678, bus=1, address=1, ports=(2,)):
    return SimpleNamespace(idVendor=vid, idProduct=pid, bus=bus, address=address,
                           port_numbers=ports, ctrl_transfer=Mock(return_value=b"\x02\x00"))


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class USBTests(unittest.TestCase):
    def test_switch_control_requests_and_same_port(self):
        source = device()
        wrong = device(usb.AOA_VENDOR, 0x2D00, ports=(3,))
        target = device(usb.AOA_VENDOR, 0x2D00, address=2)
        util = Mock()
        with patch.object(usb, "modules", return_value=(SimpleNamespace(USBError=OSError), util)), \
             patch.object(usb, "devices", side_effect=[[], [wrong, target]]):
            self.assertIs(usb.switch(source), target)
        calls = source.ctrl_transfer.call_args_list
        self.assertEqual(calls[0].args, (0xC0, 51, 0, 0, 2))
        self.assertEqual(len(calls), 8)
        for index, value in enumerate(usb.IDENTITY):
            self.assertEqual(calls[index + 1].args, (0x40, 52, 0, index, value.encode() + b"\0"))
        self.assertEqual(calls[-1].args, (0x40, 53, 0, 0, b""))
        util.dispose_resources.assert_called_once_with(source)

    def test_unsupported_protocol_does_not_switch(self):
        source = device()
        source.ctrl_transfer.return_value = b"\0\0"
        util = Mock()
        with patch.object(usb, "modules", return_value=(Mock(), util)), patch.object(usb, "devices", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "protocol"):
                usb.switch(source)
        self.assertEqual(source.ctrl_transfer.call_count, 1)
        util.dispose_resources.assert_called_once()

    def test_switch_times_out(self):
        with patch.object(usb, "modules", return_value=(Mock(), Mock())), patch.object(usb, "devices", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                usb.switch(device(), timeout=0)

    def test_ambiguous_selection_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            usb.choose([device(), device()])

    def test_cli_never_switches_implicitly(self):
        source = device()
        with patch("pocketpull.cli.devices", return_value=[source]), patch("pocketpull.cli.switch") as switch:
            self.assertEqual(main(["probe", "--vid", "0x1234", "--pid", "0x5678"]), 1)
            switch.assert_not_called()

    def test_cli_requires_explicit_pair(self):
        with self.assertRaises(SystemExit):
            main(["probe", "--switch"])

    def test_zero_length_packets_do_not_end_download(self):
        from pathlib import Path
        import tempfile
        from pocketpull.protocol import Connection
        from test_protocol import transfer

        data = bytes(i % 251 for i in range(199718))
        wire = transfer(data)
        packets = []
        for index in range(0, len(wire), 16384):
            packets.extend([wire[index:index + 16384], b"", b""])
        transport = usb.USBTransport.__new__(usb.USBTransport)
        transport.timeout = 1000
        transport.input = SimpleNamespace(read=Mock(side_effect=packets))
        transport.output = SimpleNamespace(write=lambda payload, **kwargs: len(payload))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "test.jpg"
            result = LegacyConnection(transport).pull(destination)
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(result["bytes"], 199718)

    def test_zero_length_packets_do_not_reset_timeout(self):
        transport = usb.USBTransport.__new__(usb.USBTransport)
        transport.timeout = 10
        transport.input = SimpleNamespace(read=Mock(return_value=b""))
        with patch.object(usb.time, "monotonic", side_effect=[0, 0, 0.005, 0.011]):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                transport.read()
        self.assertEqual(transport.input.read.call_count, 2)
        timeouts = [call.kwargs["timeout"] for call in transport.input.read.call_args_list]
        self.assertGreater(timeouts[0], timeouts[1])

    def test_usb_disconnect_still_fails_immediately(self):
        transport = usb.USBTransport.__new__(usb.USBTransport)
        transport.timeout = 1000
        transport.input = SimpleNamespace(read=Mock(side_effect=[b"", OSError("device disconnected")]))
        with self.assertRaisesRegex(OSError, "disconnected"):
            transport.read()

    def test_bulk_interface_zero_only(self):
        source = device(usb.AOA_VENDOR, 0x2D01)
        incoming = SimpleNamespace(bmAttributes=2, bEndpointAddress=0x81, read=Mock(return_value=b"abc"))
        outgoing = SimpleNamespace(bmAttributes=2, bEndpointAddress=1, write=Mock(return_value=3))
        config = Mock()
        config.__getitem__ = Mock(return_value=[incoming, outgoing])
        source.get_active_configuration = Mock(return_value=config)
        util = Mock(endpoint_type=lambda value: value & 3, endpoint_direction=lambda value: value & 0x80)
        with patch.object(usb, "modules", return_value=(SimpleNamespace(USBError=OSError), util)):
            transport = usb.USBTransport(source)
            self.assertEqual(transport.read(), b"abc")
            transport.write(b"abc")
            transport.close()
        config.__getitem__.assert_called_once_with((0, 0))
        util.claim_interface.assert_called_once_with(source, 0)
        util.release_interface.assert_called_once_with(source, 0)


if __name__ == "__main__":
    unittest.main()
