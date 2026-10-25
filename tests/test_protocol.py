import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from pocketpull.protocol import Connection, ProtocolError, HELLO, META, DATA, END, ERROR, MAX_FRAME


def frame(kind, data=b""):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    return struct.pack(">BI", kind, len(data)) + data


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class Transport:
    def __init__(self, data, fragment=16384):
        self.data, self.fragment, self.writes = data, fragment, []

    def read(self):
        data, self.data = self.data[:self.fragment], self.data[self.fragment:]
        return data

    def write(self, data):
        self.writes.append(data)


def transfer(data, digest=None, count=None):
    return (frame(META, {"name": "../../unsafe", "size": None})
            + b"".join(frame(DATA, data[i:i + MAX_FRAME]) for i in range(0, len(data), MAX_FRAME))
            + frame(END, {"bytes": len(data) if count is None else count,
                          "sha256": hashlib.sha256(data).hexdigest() if digest is None else digest}))


class ProtocolTests(unittest.TestCase):
    def test_fragmented_and_coalesced_frames(self):
        for fragment in (1, 2, 5, 512, 16384):
            connection = LegacyConnection(Transport(frame(HELLO, {"version": 1}) + frame(META, {"selected": False}), fragment))
            connection.hello()
            self.assertEqual(connection.selected(), {"selected": False})

    def test_verified_binary_large_and_empty(self):
        for data in (b"", bytes(range(256)) * 300):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "chosen.bin"
                result = LegacyConnection(Transport(transfer(data), 71)).pull(path)
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(result["bytes"], len(data))
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failures_leave_no_file_or_partial(self):
        data = b"hello"
        invalid = [transfer(data, digest="0" * 64), transfer(data, count=6),
                   transfer(data)[:-1], frame(META, {}) + frame(ERROR, b"Approval revoked"),
                   frame(META, {}) + frame(HELLO), frame(META, {}) + frame(END, b"[]"),
                   frame(META, {}) + frame(END, {"bytes": True, "sha256": hashlib.sha256(data).hexdigest()})]
        for wire in invalid:
            with self.subTest(wire=wire), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ProtocolError):
                    LegacyConnection(Transport(wire)).pull(Path(directory) / "file")
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_keyboard_interrupt_cleans_partial(self):
        transport = Transport(frame(META, {}) + frame(DATA, b"partial"))
        original_read = transport.read
        def read():
            if not transport.data:
                raise KeyboardInterrupt()
            return original_read()
        transport.read = read
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                LegacyConnection(transport).pull(Path(directory) / "file")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_file_and_symlink_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing"
            path.write_bytes(b"original")
            for destination in (path, Path(directory) / "link"):
                if destination != path:
                    destination.symlink_to(Path(directory) / "missing")
                with self.assertRaises(FileExistsError):
                    LegacyConnection(Transport(transfer(b"new"))).pull(destination)
            self.assertEqual(path.read_bytes(), b"original")

    def test_destination_creation_race_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race"
            def race(*args):
                path.write_bytes(b"other writer")
                raise FileExistsError()
            with patch("pocketpull.protocol.os.link", side_effect=race), self.assertRaises(FileExistsError):
                LegacyConnection(Transport(transfer(b"new"))).pull(path)
            self.assertEqual(path.read_bytes(), b"other writer")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_frames_and_version(self):
        for wire in (struct.pack(">BI", DATA, MAX_FRAME + 1), frame(HELLO, b"not json"),
                     frame(HELLO, b"[]"), frame(HELLO, {"version": 6})):
            with self.assertRaises(ProtocolError):
                LegacyConnection(Transport(wire)).hello()

    def test_oversized_outgoing_frame_rejected(self):
        with self.assertRaises(ProtocolError):
            LegacyConnection(Transport(b"")).send(DATA, b"a" * (MAX_FRAME + 1))


if __name__ == "__main__":
    unittest.main()
