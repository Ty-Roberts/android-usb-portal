import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pocketpull.protocol import (Connection, TransferCancelled, DATA, END, META, GET, PUSH, CANCEL,
                                 HELLO, ERROR, local_destination)
from test_folders_push import Phone
from test_protocol import frame, transfer, Transport


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class CancellablePhone(Phone):
    def __init__(self):
        super().__init__()
        self.download = None
        self.version = 3

    def next_chunk(self):
        path, content, offset = self.download
        if offset == len(content):
            self.reply(END, {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            self.download = None
        else:
            chunk = content[offset:offset + 16379]
            self.download = (path, content, offset + len(chunk))
            self.reply(DATA, chunk)

    def write(self, data):
        kind = data[0]
        if kind == HELLO:
            self.reply(HELLO, {"version": self.version})
        elif kind == GET:
            request = json.loads(data[5:])
            assert request["acknowledge"] is True
            path = request["path"]
            self.reply(META, self.metadata(path))
            self.download = (path, self.tree[path], 0)
            self.next_chunk()
        elif kind == META:
            assert self.download is not None
            request = json.loads(data[5:])
            assert request["bytes"] == self.download[2]
            self.next_chunk()
        elif kind == CANCEL:
            self.download = self.active = None
            self.reply(END, {"cancelled": True})
        elif kind == ERROR:
            self.active = None
            self.reply(ERROR, b"Upload cancelled")
        else:
            super().write(data)


class CancellationTests(unittest.TestCase):
    def test_cancel_download_cleans_output_and_reuses_session(self):
        phone = CancellablePhone()
        cancel = False
        connection = LegacyConnection(phone, cancelled=lambda: cancel)
        connection.hello()
        def progress(name, count, expected):
            nonlocal cancel
            if count > 0:
                cancel = True
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cancelled.bin"
            with self.assertRaises(TransferCancelled):
                connection.pull(target, "docs/photo.bin", progress)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertIsNone(phone.download)
            cancel = False
            connection.hello()
            result = connection.pull(target, "docs/photo.bin")
            self.assertEqual(target.read_bytes(), phone.tree["docs/photo.bin"])
            self.assertEqual(result["bytes"], 38400)

    def test_cancel_upload_removes_remote_partial_and_reuses_session(self):
        phone = CancellablePhone()
        cancel = False
        connection = LegacyConnection(phone, cancelled=lambda: cancel)
        connection.hello()
        def progress(name, count, expected):
            nonlocal cancel
            if count > 0:
                cancel = True
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            source.write_bytes(b"a" * 50000)
            with self.assertRaises(TransferCancelled):
                connection.push(source, "new.bin", progress)
            self.assertNotIn("new.bin", phone.tree)
            self.assertIsNone(phone.active)
            cancel = False
            connection.hello()
            connection.push(source, "new.bin")
            self.assertEqual(phone.tree["new.bin"], source.read_bytes())

    def test_v2_download_cancel_drains_stream_before_reconnect(self):
        wire = transfer(b"a" * 50000) + frame(HELLO, {"version": 2})
        cancel = False
        connection = LegacyConnection(Transport(wire), cancelled=lambda: cancel)
        connection.version = 2
        def progress(name, count, expected):
            nonlocal cancel
            cancel = count > 0
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TransferCancelled):
                connection.pull_file(Path(directory) / "file", progress=progress)
            self.assertEqual(list(Path(directory).iterdir()), [])
            cancel = False
            connection.hello()
            self.assertEqual(connection.version, 2)

    def test_v2_upload_cancel_cleans_error_reply(self):
        phone = CancellablePhone()
        phone.version = 2
        cancel = False
        connection = LegacyConnection(phone, cancelled=lambda: cancel)
        connection.hello()
        def progress(name, count, expected):
            nonlocal cancel
            cancel = count > 0
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(b"a" * 40000)
            with self.assertRaises(TransferCancelled):
                connection.push(source, "uploaded", progress)
            cancel = False
            connection.hello()
            self.assertIsNone(phone.active)
            self.assertNotIn("uploaded", phone.tree)

    def test_cancel_before_operation_does_not_send_frames(self):
        transport = Transport(b"")
        connection = LegacyConnection(transport, cancelled=lambda: True)
        with self.assertRaises(TransferCancelled):
            connection.pull("unused")
        self.assertEqual(transport.writes, [])


class ConflictTests(unittest.TestCase):
    def test_local_keep_both_preserves_file_and_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "photo.jpg"
            target.write_bytes(b"original")
            (Path(directory) / "photo (2).jpg").write_bytes(b"second")
            connection = LegacyConnection(Transport(transfer(b"new")))
            result = connection.pull(target, conflict="keep-both")
            self.assertEqual(Path(result["destination"]).name, "photo (3).jpg")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(Path(result["destination"]).read_bytes(), b"new")

    def test_local_skip_does_not_request_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing"
            target.write_bytes(b"keep")
            transport = Transport(b"")
            result = LegacyConnection(transport).pull(target, conflict="skip")
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(transport.writes, [])
            self.assertEqual(target.read_bytes(), b"keep")

    def test_directory_keep_both_and_skip(self):
        phone = Phone()
        connection = LegacyConnection(phone)
        connection.hello()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "photos"
            target.mkdir()
            result = connection.pull(target, conflict="keep-both")
            self.assertEqual(Path(result["destination"]).name, "photos (2)")
            self.assertTrue((target.parent / "photos (2)/docs/photo.bin").is_file())
            self.assertEqual(connection.pull(target, conflict="skip")["status"], "skipped")
            self.assertEqual(list(target.iterdir()), [])

    def test_phone_keep_both_and_skip(self):
        phone = Phone()
        connection = LegacyConnection(phone)
        connection.hello()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "zero"
            source.write_bytes(b"new")
            self.assertEqual(connection.push(source, conflict="skip")["status"], "skipped")
            result = connection.push(source, conflict="keep-both")
            self.assertEqual(result["remote"], "zero (2)")
            self.assertEqual(phone.tree["zero"], b"")
            self.assertEqual(phone.tree["zero (2)"], b"new")

    def test_upload_skip_merges_directories_without_replacing_files(self):
        phone = Phone()
        connection = LegacyConnection(phone)
        connection.hello()
        original = phone.tree["docs/photo.bin"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "docs"
            source.mkdir()
            (source / "photo.bin").write_bytes(b"replacement")
            (source / "new.txt").write_bytes(b"new")
            result = connection.push(source, conflict="skip")
            self.assertEqual(result["skipped"], 1)
            self.assertEqual(phone.tree["docs/photo.bin"], original)
            self.assertEqual(phone.tree["docs/new.txt"], b"new")

    def test_broken_symlink_is_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "broken"
            target.symlink_to("missing")
            self.assertIsNone(local_destination(target, "skip"))
            self.assertEqual(local_destination(target, "keep-both").name, "broken (2)")
            self.assertTrue(target.is_symlink())
