import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pocketpull.protocol import Connection, TransferCancelled, ProtocolError, HELLO, LIST, GET, META, PUSH, DATA, END, CANCEL, ERROR
from test_cancel_conflicts import CancellablePhone


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class ResumePhone(CancellablePhone):
    def __init__(self):
        super().__init__()
        self.parts = {}
        self.active_key = None
        self.offsets = []

    def write(self, data):
        kind = data[0]
        request = json.loads(data[5:]) if data[5:] and kind not in (DATA, ERROR) else {}
        if kind == HELLO: self.reply(HELLO, {"version": 4, "device_id": "phone"})
        elif kind == LIST: self.reply(META, {"selected": True, "scope_id": "scope", "type": "directory"})
        elif kind == GET:
            path = request["path"]; offset = request.get("offset", 0)
            content = self.tree[path]
            if offset > len(content) or request.get("prefix_sha256", hashlib.sha256(b"").hexdigest()) != hashlib.sha256(content[:offset]).hexdigest():
                self.reply(ERROR, b"Operation stopped: RESUME_MISMATCH: changed prefix"); return
            self.offsets.append(offset)
            self.reply(META, {**self.metadata(path), "offset": offset})
            self.download = (path, content, offset); self.next_chunk()
        elif kind == PUSH and request.get("resume"):
            key = (request["path"], request["sha256"])
            content = self.parts.setdefault(key, bytearray())
            self.active = (request["path"], request["size"], content)
            self.active_key = key
            self.reply(META, {"ready": True, "offset": len(content), "prefix_sha256": hashlib.sha256(content).hexdigest()})
        elif kind == CANCEL:
            if self.active_key is not None and request.get("discard"): self.parts.pop(self.active_key, None)
            self.active = self.download = self.active_key = None
            self.reply(END, {"cancelled": True})
        elif kind == END:
            super().write(data)
            if self.active_key is not None: self.parts.pop(self.active_key, None)
            self.active_key = None
        else: super().write(data)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"POCKET_DIMENSION_CACHE": self.directory.name + "/cache"})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.phone = ResumePhone()
        self.cancel = False
        self.connection = LegacyConnection(self.phone, cancelled=lambda: self.cancel)
        self.connection.hello()

    def progress(self, name, count, total):
        if count >= 16379: self.cancel = True

    def test_download_resumes_verified_prefix(self):
        target = Path(self.directory.name) / "result.bin"
        with self.assertRaises(TransferCancelled): self.connection.pull(target, "docs/photo.bin", self.progress, resume=True)
        self.assertFalse(target.exists())
        self.cancel = False
        result = self.connection.pull(target, "docs/photo.bin", resume=True)
        self.assertGreater(result["resumed_bytes"], 0)
        self.assertEqual(target.read_bytes(), self.phone.tree["docs/photo.bin"])
        self.assertEqual(list((Path(self.directory.name) / "cache/resume").iterdir()), [])

    def test_changed_source_prefix_is_refused_and_cache_discarded(self):
        target = Path(self.directory.name) / "result.bin"
        with self.assertRaises(TransferCancelled): self.connection.pull(target, "docs/photo.bin", self.progress, resume=True)
        original = self.phone.tree["docs/photo.bin"]
        self.phone.tree["docs/photo.bin"] = b"changed" + original[7:]
        self.cancel = False
        with self.assertRaisesRegex(ProtocolError, "RESUME_MISMATCH"): self.connection.pull(target, "docs/photo.bin", resume=True)
        self.assertFalse(target.exists())
        result = self.connection.pull(target, "docs/photo.bin", resume=True)
        self.assertEqual(result["resumed_bytes"], 0)
        self.assertEqual(target.read_bytes(), self.phone.tree["docs/photo.bin"])

    def test_upload_resumes_private_prefix(self):
        source = Path(self.directory.name) / "source.bin"
        source.write_bytes(bytes(range(251)) * 300)
        with self.assertRaises(TransferCancelled): self.connection.push(source, "uploaded", self.progress, resume=True)
        self.assertNotIn("uploaded", self.phone.tree)
        self.assertTrue(self.phone.parts)
        self.cancel = False
        result = self.connection.push(source, "uploaded", resume=True)
        self.assertGreater(result["resumed_bytes"], 0)
        self.assertEqual(self.phone.tree["uploaded"], source.read_bytes())
        self.assertEqual(self.phone.parts, {})

    def test_changed_local_upload_starts_new_identity(self):
        source = Path(self.directory.name) / "source.bin"
        source.write_bytes(b"a" * 40000)
        with self.assertRaises(TransferCancelled): self.connection.push(source, "uploaded", self.progress, resume=True)
        source.write_bytes(b"b" * 40000); self.cancel = False
        result = self.connection.push(source, "uploaded", resume=True)
        self.assertEqual(result["resumed_bytes"], 0)
        self.assertEqual(self.phone.tree["uploaded"], b"b" * 40000)

    def test_cache_symlinks_are_rejected(self):
        cache = Path(self.directory.name) / "cache"
        cache.mkdir(); (cache / "resume").symlink_to(Path(self.directory.name), target_is_directory=True)
        with self.assertRaises(OSError): self.connection.pull(Path(self.directory.name) / "result", "docs/photo.bin", resume=True)

    def test_tree_plan_counts_nested_bytes(self):
        result = self.connection.plan("")
        self.assertEqual(result, {"bytes": 38400, "files": 2, "known": True})

    def test_cancel_during_upload_prefix_verification_confirms_boundary(self):
        source = Path(self.directory.name) / "source.bin"
        source.write_bytes(b"a" * 40000)
        with self.assertRaises(TransferCancelled): self.connection.push(source, "uploaded", self.progress, resume=True)
        self.cancel = False
        with patch("pocketpull.resume.hash_stream", side_effect=[hashlib.sha256(source.read_bytes()), TransferCancelled("cancelled")]):
            with self.assertRaises(TransferCancelled): self.connection.push(source, "uploaded", resume=True)
        self.assertIsNone(self.phone.active)
        self.assertTrue(self.connection.selected()["selected"])

    def test_unconfirmed_upload_cancel_is_an_error(self):
        source = Path(self.directory.name) / "source.bin"
        source.write_bytes(b"a" * 40000)
        with patch("pocketpull.resume.hash_stream", side_effect=[hashlib.sha256(source.read_bytes()), TransferCancelled("cancelled")]), patch.object(self.connection, "cancel_at_boundary", side_effect=ProtocolError("Cancellation not confirmed")):
            with self.assertRaisesRegex(ProtocolError, "not confirmed"): self.connection.push(source, "uploaded", resume=True)
