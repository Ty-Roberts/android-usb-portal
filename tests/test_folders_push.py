import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from pocketpull.protocol import (Connection, ProtocolError, HELLO, LIST, GET, META, DATA,
                                 END, ERROR, BROWSE, PUSH, MKDIR, STAT, safe_path)
from test_protocol import frame


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class Phone:
    """Interactive fake exercising entire client requests, streaming, and acknowledgments."""
    def __init__(self):
        self.tree = {"": None, "docs": None, "empty-folder": None,
                     "docs/photo.bin": bytes(range(256)) * 150, "zero": b""}
        self.output = bytearray()
        self.active = None
        self.bad_checksum = False
        self.reject_upload = False
        self.writes = []

    def reply(self, kind, value):
        self.output.extend(frame(kind, value))

    def metadata(self, path):
        value = self.tree[path]
        return {"name": path.rsplit("/", 1)[-1] or "Shared", "type": "directory" if value is None else "file",
                "size": None if value is None else len(value), "writable": True}

    def write(self, data):
        kind, length = struct.unpack(">BI", data[:5])
        payload = data[5:]
        assert length == len(payload)
        self.writes.append(kind)
        obj = json.loads(payload) if payload and kind not in (DATA, ERROR) else {}
        path = obj.get("path", "")
        if kind == HELLO:
            self.reply(HELLO, {"version": 2})
        elif kind == STAT:
            if path not in self.tree:
                self.reply(ERROR, b"No such file")
            else:
                self.reply(META, self.metadata(path))
        elif kind == BROWSE:
            prefix = path + "/" if path else ""
            names = [p for p in self.tree if p.startswith(prefix) and p != path and "/" not in p[len(prefix):]]
            for child in sorted(names):
                self.reply(META, self.metadata(child))
            self.reply(END, {"entries": len(names)})
        elif kind == GET:
            content = self.tree[path]
            self.reply(META, self.metadata(path))
            for i in range(0, len(content), 16379):
                self.reply(DATA, content[i:i + 16379])
            self.reply(END, {"bytes": len(content), "sha256": "bad" if self.bad_checksum else hashlib.sha256(content).hexdigest()})
        elif kind == MKDIR:
            if path in self.tree and self.tree[path] is not None:
                self.reply(ERROR, b"Destination is a file")
            else:
                self.tree[path] = None
                self.reply(META, self.metadata(path))
        elif kind == PUSH:
            if path in self.tree or self.reject_upload:
                self.reply(ERROR, b"Upload refused")
            else:
                self.active = (path, obj["size"], bytearray())
                self.reply(META, {"ready": True})
        elif kind == DATA:
            self.active[2].extend(payload)
            self.reply(META, {"bytes": len(self.active[2])})
        elif kind == END:
            path, size, content = self.active
            assert size == len(content) == obj["bytes"]
            assert hashlib.sha256(content).hexdigest() == obj["sha256"]
            self.tree[path] = bytes(content)
            self.active = None
            self.reply(END, {**obj, "sha256": "bad" if self.bad_checksum else obj["sha256"]})
        else:
            raise AssertionError(kind)

    def read(self):
        value = bytes(self.output[:67])
        del self.output[:67]
        return value


class FolderTests(unittest.TestCase):
    def connect(self):
        self.phone = Phone()
        connection = LegacyConnection(self.phone)
        connection.hello()
        return connection

    def test_recursive_pull_nested_empty_and_binary(self):
        connection = self.connect()
        updates = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "download"
            result = connection.pull(target, progress=lambda *args: updates.append(args))
            self.assertEqual((target / "docs/photo.bin").read_bytes(), self.phone.tree["docs/photo.bin"])
            self.assertEqual((target / "zero").read_bytes(), b"")
            self.assertTrue((target / "empty-folder").is_dir())
            self.assertEqual({key: result[key] for key in ("type", "files", "bytes")}, {"type": "directory", "files": 2, "bytes": 38400})
            self.assertTrue(any(update[0] == "docs/photo.bin" and update[1] == 38400 for update in updates))

    def test_subdirectory_pull(self):
        connection = self.connect()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "docs"
            self.assertEqual(connection.pull(target, remote="docs")["files"], 1)
            self.assertTrue((target / "photo.bin").exists())

    def test_corruption_discards_whole_new_directory(self):
        connection = self.connect()
        self.phone.bad_checksum = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProtocolError):
                connection.pull(Path(directory) / "broken")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_directory_preserved(self):
        connection = self.connect()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing"
            target.mkdir()
            original = target / "keep"
            original.write_text("original")
            with self.assertRaises(FileExistsError):
                connection.pull(target)
            self.assertEqual(original.read_text(), "original")

    def test_push_binary_empty_and_folder(self):
        connection = self.connect()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "upload"
            root.mkdir()
            (root / "nested").mkdir()
            (root / "empty-dir").mkdir()
            (root / "nested/data").write_bytes(b"binary\x00" * 9000)
            (root / "zero").touch()
            updates = []
            result = connection.push(root, progress=lambda *args: updates.append(args))
            self.assertEqual(result["files"], 2)
            self.assertEqual(self.phone.tree["upload/nested/data"], b"binary\x00" * 9000)
            self.assertEqual(self.phone.tree["upload/zero"], b"")
            self.assertIsNone(self.phone.tree["upload/empty-dir"])
            self.assertTrue(updates)

    def test_upload_refusal_leaves_source_unchanged(self):
        connection = self.connect()
        self.phone.reject_upload = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local"
            path.write_bytes(b"keep")
            with self.assertRaisesRegex(ProtocolError, "refused"):
                connection.push(path)
            self.assertEqual(path.read_bytes(), b"keep")
            self.assertNotIn(DATA, self.phone.writes)

    def test_upload_checksum_reply_verified(self):
        connection = self.connect()
        self.phone.bad_checksum = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local"
            path.write_bytes(b"keep")
            with self.assertRaisesRegex(ProtocolError, "Checksum"):
                connection.push(path)

    def test_symlinks_never_uploaded(self):
        connection = self.connect()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "link"
            path.symlink_to("/etc/hosts")
            with self.assertRaisesRegex(ProtocolError, "symbolic"):
                connection.push(path)
            self.assertNotIn(PUSH, self.phone.writes)

    def test_v1_has_clear_upgrade_message(self):
        connection = LegacyConnection(Phone())
        for method in (lambda: connection.stat(), lambda: connection.browse(), lambda: connection.push("anything")):
            with self.assertRaisesRegex(ProtocolError, "Update the Android APK"):
                method()

    def test_path_traversal_and_unsafe_names_rejected(self):
        for value in ("../secret", "/sdcard", "a/../b", "a//b", "a\\b", "a/", "a\x00b", "a\nb"):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                safe_path(value)
        self.assertEqual(safe_path("pictures/猫.jpg"), "pictures/猫.jpg")

    def test_untrusted_listing_names_counts_and_duplicates(self):
        from test_protocol import Transport
        variants = [frame(META, {"name": "../escape", "type": "file"}),
                    frame(META, {"name": "safe", "type": "symlink"}),
                    frame(END, {"entries": 1}),
                    frame(META, {"name": "same", "type": "file"}) * 2]
        for response in variants:
            connection = LegacyConnection(Transport(response))
            connection.version = 2
            with self.assertRaises(ProtocolError):
                connection.browse()


if __name__ == "__main__":
    unittest.main()
