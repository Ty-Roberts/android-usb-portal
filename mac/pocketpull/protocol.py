"""Bounded USB framing, scoped paths, and verified bidirectional transfers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

HELLO, LIST, GET, META, DATA, END, ERROR = range(1, 8)
BROWSE, PUSH, MKDIR, STAT, CANCEL = range(8, 13)
MAX_FRAME = 16379
BATCH_FRAMES = 16
MAX_ENTRIES = 100000
MAX_DEPTH = 128


class ProtocolError(Exception):
    pass


class RemoteError(ProtocolError):
    """An explicit ERROR frame, distinct from malformed framing or a transport failure."""


class TransferCancelled(Exception):
    """Raised only after the current protocol operation is synchronized/finished."""


def occupied(path):
    return path.exists() or path.is_symlink()


def alternate_name(name, number, directory=False):
    safe_name(name)
    suffix = "" if directory else Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    return safe_name(f"{stem} ({number}){suffix}")


def local_destination(path, policy, directory=False):
    path = Path(path)
    if not occupied(path):
        return path
    if policy == "skip":
        return None
    if policy == "keep-both":
        for number in range(2, 10002):
            candidate = path.with_name(alternate_name(path.name, number, directory))
            if not occupied(candidate):
                return candidate
        raise ProtocolError("Could not find an unused destination name")
    raise FileExistsError(f"Destination already exists: {path}")


def safe_name(value):
    if (not isinstance(value, str) or not value or len(value) > 512 or value in (".", "..")
            or any(c in value for c in '/\\\x00')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ProtocolError("Unsafe filename from peer")
    return value


def safe_path(value):
    if not isinstance(value, str) or len(value) > 8192 or value.startswith("/") or len(value.split("/")) > MAX_DEPTH:
        raise ProtocolError("Paths must be relative to the phone's shared selection")
    if value:
        for component in value.split("/"):
            safe_name(component)
    return value


def joined(parent, name):
    return f"{parent}/{safe_name(name)}" if parent else safe_name(name)


def parse_object(payload):
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON from phone") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("Expected a JSON object")
    return obj


def check_digest(end, total, digest):
    if type(end.get("bytes")) is not int or end["bytes"] != total or end.get("sha256") != digest:
        raise ProtocolError("Checksum or byte count mismatch; transfer discarded")


class Connection:
    def __init__(self, transport, cancelled=None, *, allow_legacy=False, security=None):
        self.transport = transport
        self.allow_legacy = allow_legacy
        self.security = security
        self.secure_transport = None
        self.buffer = bytearray()
        self.version = 1
        self.cancelled = cancelled or (lambda: False)
        self.remote_names = {}
        self.device_id = None
        self.device_info = {}
        self.features = set()
        self.batching_enabled = True

    def send(self, kind, payload=b""):
        if len(payload) > MAX_FRAME:
            raise ProtocolError("Frame too large")
        self.transport.write(struct.pack(">BI", kind, len(payload)) + payload)

    def send_json(self, kind, obj):
        self.send(kind, json.dumps(obj, ensure_ascii=True).encode())

    def _exact(self, length):
        while len(self.buffer) < length:
            chunk = self.transport.read()
            if not chunk:
                raise ProtocolError("Connection closed during transfer")
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def receive(self):
        kind, length = struct.unpack(">BI", self._exact(5))
        if length > MAX_FRAME:
            raise ProtocolError("Peer sent an oversized frame")
        payload = self._exact(length)
        if kind == ERROR:
            raise RemoteError(payload.decode("utf-8", errors="replace"))
        return kind, payload

    def json_response(self, expected):
        kind, payload = self.receive()
        if kind != expected:
            raise ProtocolError(f"Unexpected frame {kind}, expected {expected}")
        return parse_object(payload)

    def hello(self, *, public_only=False, pair_fingerprint=None, revoke=False):
        self.send(HELLO)
        response = self.json_response(HELLO)
        version = response.get("version")
        if type(version) is not int or version not in (1, 2, 3, 4, 5):
            raise ProtocolError("Unsupported Pocket Dimension protocol version")
        self.version = version
        features = response.get("features", [])
        self.features = {item for item in features if isinstance(item, str)} if isinstance(features, list) else set()
        self.device_id = response.get("device_id")
        self.device_info = {key: response.get(key) for key in ("manufacturer", "model")}
        if public_only:
            return
        if version < 5:
            if not self.allow_legacy:
                raise ProtocolError("Update the Android app: authenticated encryption is required")
            return
        if self.secure_transport is None:
            from .security import HostSecurity
            # Keychain may prompt or be denied. Resolve it before putting the
            # accessory into a TLS handshake with its bounded deadline.
            owner = self.security or HostSecurity()
            self.send(13)
            if self.buffer: raise ProtocolError("Unexpected data before TLS upgrade")
            channel = owner.connect(self.transport, mode="pair" if pair_fingerprint else "revoke" if revoke else "connect", expected_phone=pair_fingerprint)
            if revoke: return
            self.secure_transport = channel
            self.transport = channel
            self.hello()

    def require_security(self):
        if not self.allow_legacy and self.secure_transport is None:
            raise ProtocolError("An authenticated encrypted session is required")

    def close(self):
        if self.secure_transport:
            self.secure_transport.close()


    def batch_options(self):
        return {"batch_frames": BATCH_FRAMES} if self.version >= 4 and self.batching_enabled and "batch-ack" in self.features else {}

    def verify_batch_mode(self, metadata, frames):
        if frames and (type(metadata.get("batch_frames")) is not int or metadata["batch_frames"] != frames):
            raise ProtocolError("Phone did not accept the bounded batch size")

    def receive_download_batch(self, frames):
        if not frames:
            kind, payload = self.receive()
            return kind, payload, None
        chunks = []
        while True:
            kind, payload = self.receive()
            if kind == DATA:
                if not payload or len(chunks) >= frames:
                    raise ProtocolError("Phone exceeded the bounded download batch")
                chunks.append(payload)
            elif kind == META and chunks:
                marker = parse_object(payload)
                return DATA, b"".join(chunks), marker
            elif kind == END and not chunks:
                return kind, payload, None
            else:
                raise ProtocolError("Missing download batch boundary")

    def verify_download_boundary(self, marker, total):
        if marker is not None and (marker.get("batch") is not True or type(marker.get("bytes")) is not int or marker["bytes"] != total):
            raise ProtocolError("Download batch acknowledgment count mismatch")

    def send_upload_batch(self, payload, total, frames):
        if not payload or len(payload) > MAX_FRAME * (frames or 1):
            raise ProtocolError("Invalid upload batch size")
        for offset in range(0, len(payload), MAX_FRAME):
            self.send(DATA, payload[offset:offset + MAX_FRAME])
        if frames:
            self.send_json(META, {"batch": True, "bytes": total})
        response = self.json_response(META)
        if type(response.get("bytes")) is not int or response["bytes"] != total:
            raise ProtocolError("Upload acknowledgment mismatch")

    def require_v2(self):
        self.require_security()
        if self.version < 2:
            raise ProtocolError("Update the Android APK for folders, browsing, and uploads")

    def selected(self):
        self.require_security()
        self.send(LIST)
        return self.json_response(META)

    def stat(self, remote=""):
        self.require_v2()
        self.send_json(STAT, {"path": safe_path(remote)})
        return self.json_response(META)

    def browse(self, remote=""):
        self.require_v2()
        self.send_json(BROWSE, {"path": safe_path(remote)})
        entries, names = [], set()
        while True:
            kind, payload = self.receive()
            obj = parse_object(payload)
            if kind == END:
                if type(obj.get("entries")) is not int or obj["entries"] != len(entries):
                    raise ProtocolError("Directory listing count mismatch")
                return entries
            if kind != META or len(entries) >= MAX_ENTRIES:
                raise ProtocolError("Invalid or excessive directory listing")
            name = safe_name(obj.get("name"))
            if name in names or obj.get("type") not in ("file", "directory"):
                raise ProtocolError("Duplicate or invalid directory entry")
            names.add(name)
            entries.append(obj)

    def plan(self, remote=""):
        info = self.stat(remote)
        if info.get("type") == "file":
            size = info.get("size")
            return {"bytes": size if type(size) is int and size >= 0 else 0, "files": 1, "known": type(size) is int and size >= 0}
        pending = [(remote, 0)]; total = files = entries = 0; known = True
        while pending:
            self.check_cancel()
            path, depth = pending.pop()
            if depth > MAX_DEPTH: raise ProtocolError("Directory nesting limit exceeded")
            for child in self.browse(path):
                entries += 1
                if entries > MAX_ENTRIES: raise ProtocolError("Directory entry limit exceeded")
                if child["type"] == "directory": pending.append((joined(path, child["name"]), depth + 1))
                else:
                    files += 1; size = child.get("size")
                    if type(size) is int and size >= 0: total += size
                    else: known = False
        return {"bytes": total, "files": files, "known": known}

    def check_cancel(self):
        if self.cancelled():
            raise TransferCancelled("Cancelled; completed transfers are kept")

    def cancel_at_boundary(self):
        if self.version >= 3:
            self.send(CANCEL)
            if self.json_response(END).get("cancelled") is not True:
                raise ProtocolError("Phone did not confirm cancellation")
        else:
            # v2 upload receiver consumes ERROR, deletes the partial file, and replies ERROR.
            self.send(ERROR, b"Upload cancelled by host")
            try:
                self.receive()
            except RemoteError:
                return
            raise ProtocolError("Phone did not confirm upload cancellation")

    def pull(self, destination, remote="", progress=None, conflict="error", resume=False):
        self.check_cancel()
        if remote:
            self.require_v2()
            safe_path(remote)
        if self.version >= 2 and self.stat(remote).get("type") == "directory":
            return self.pull_directory(destination, remote, progress, conflict, resume)
        return self.pull_file(destination, remote, progress, conflict, resume)

    def pull_file(self, destination, remote="", progress=None, conflict="error", resume=False):
        self.require_security()
        self.check_cancel()
        if resume and self.version >= 4:
            from .resume import download
            return download(self, destination, remote, progress, conflict)
        original = Path(destination)
        destination = local_destination(original, conflict)
        if destination is None:
            return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "destination": str(original)}
        # Create local temporary output before starting USB streaming.
        temporary = None
        active = False
        ack_pending = False
        options = self.batch_options()
        frames = options.get("batch_frames", 0)
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pocket-dimension-", delete=False) as out:
                temporary = Path(out.name)
                if self.version >= 3:
                    self.send_json(GET, {"path": safe_path(remote), "acknowledge": True, **options})
                elif remote:
                    self.send_json(GET, {"path": safe_path(remote)})
                else:
                    self.send(GET)
                metadata = self.json_response(META)
                active = True
                self.verify_batch_mode(metadata, frames)
                digest, total = hashlib.sha256(), 0
                expected = metadata.get("size")
                if type(expected) is not int or expected < 0:
                    expected = None
                name = remote or metadata.get("name", "file")
                if progress:
                    progress(name, 0, expected)
                while True:
                    kind, payload, marker = self.receive_download_batch(frames)
                    if kind == DATA:
                        ack_pending = self.version >= 3
                        self.verify_download_boundary(marker, total + len(payload))
                        if self.cancelled():
                            if ack_pending:
                                self.cancel_at_boundary()
                                active = ack_pending = False
                                raise TransferCancelled("Cancelled; incomplete download discarded")
                            # Original APKs stream without acknowledgments: drain to a frame boundary.
                            continue
                        out.write(payload)
                        digest.update(payload)
                        total += len(payload)
                        if progress:
                            progress(name, total, expected)
                        if ack_pending:
                            if self.cancelled():
                                self.cancel_at_boundary()
                                active = ack_pending = False
                                raise TransferCancelled("Cancelled; incomplete download discarded")
                            self.send_json(META, {"bytes": total})
                            ack_pending = False
                    elif kind == END:
                        active = False
                        self.check_cancel()
                        check_digest(parse_object(payload), total, digest.hexdigest())
                        break
                    else:
                        raise ProtocolError(f"Unexpected transfer frame: {kind}")
                out.flush()
                os.fsync(out.fileno())
            self.check_cancel()
            # Recheck conflicts after receiving: another writer may have created the target.
            destination = local_destination(destination, conflict)
            if destination is None:
                return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "destination": str(original)}
            os.link(temporary, destination)
            if progress:
                progress(name, total, total)
            return {"name": metadata.get("name"), "type": "file", "bytes": total,
                    "sha256": digest.hexdigest(), "destination": str(destination)}
        except BaseException:
            if active and ack_pending:
                try:
                    self.cancel_at_boundary()
                except Exception:
                    pass  # Preserve the original disk/transport error.
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def pull_directory(self, destination, remote="", progress=None, conflict="error", resume=False):
        self.require_v2()
        self.check_cancel()
        original = Path(destination)
        destination = local_destination(original, conflict, directory=True)
        if destination is None:
            return {"type": "directory", "status": "skipped", "files": 0, "bytes": 0, "skipped": 1, "destination": str(original)}
        destination.mkdir()
        files, total, count = 0, 0, 0
        try:
            pending = [(safe_path(remote), destination, 0)]
            while pending:
                self.check_cancel()
                source, target, depth = pending.pop()
                if depth > MAX_DEPTH:
                    raise ProtocolError("Directory nesting limit exceeded")
                for entry in self.browse(source):
                    self.check_cancel()
                    count += 1
                    if count > MAX_ENTRIES:
                        raise ProtocolError("Directory entry limit exceeded")
                    path = target / safe_name(entry["name"])
                    child = joined(source, entry["name"])
                    if entry["type"] == "directory":
                        path.mkdir()
                        pending.append((child, path, depth + 1))
                    else:
                        result = self.pull_file(path, child, progress, resume=resume)
                        files += 1
                        total += result["bytes"]
            return {"type": "directory", "files": files, "bytes": total, "destination": str(destination)}
        except BaseException:
            shutil.rmtree(destination)
            raise

    def remote_destination(self, remote, policy, directory=False):
        if policy == "error":
            return remote
        parent, _, name = remote.rpartition("/")
        if parent not in self.remote_names:
            self.remote_names[parent] = {entry["name"]: entry["type"] for entry in self.browse(parent)}
        names = self.remote_names[parent]
        if name not in names:
            names[name] = "directory" if directory else "file"
            return remote
        if directory and policy == "skip" and names[name] == "directory":
            return remote  # Merge directories, skip existing child files.
        if policy == "skip":
            return None
        for number in range(2, 10002):
            candidate = alternate_name(name, number, directory)
            if candidate not in names:
                names[candidate] = "directory" if directory else "file"
                return joined(parent, candidate)
        raise ProtocolError("Could not find an unused phone filename")

    def mkdir(self, remote):
        self.check_cancel()
        self.require_v2()
        self.send_json(MKDIR, {"path": safe_path(remote)})
        return self.json_response(META)

    def push(self, source, remote=None, progress=None, conflict="error", resume=False):
        self.check_cancel()
        self.require_v2()
        self.remote_names = {}
        source = Path(source)
        if source.is_symlink():
            raise ProtocolError("Uploading symbolic links is not supported")
        remote = safe_path(remote if remote is not None else safe_name(source.name))
        if not remote:
            raise ProtocolError("Upload destination needs a filename or directory name")
        if source.is_dir():
            original = remote
            remote = self.remote_destination(remote, conflict, directory=True)
            if remote is None:
                return {"type": "directory", "status": "skipped", "files": 0, "bytes": 0, "skipped": 1, "remote": original}
            self.mkdir(remote)
            files, total, count, skipped = 0, 0, 0, 0
            pending = [(source, remote, 0)]
            while pending:
                self.check_cancel()
                local, phone, depth = pending.pop()
                if depth > MAX_DEPTH:
                    raise ProtocolError("Directory nesting limit exceeded")
                for child in sorted(local.iterdir()):
                    self.check_cancel()
                    count += 1
                    if count > MAX_ENTRIES or child.is_symlink():
                        raise ProtocolError("Too many entries or symbolic link in upload")
                    target = joined(phone, child.name)
                    if child.is_dir():
                        resolved = self.remote_destination(target, conflict, directory=True)
                        if resolved is None:
                            skipped += 1
                            continue
                        target = resolved
                        self.mkdir(target)
                        pending.append((child, target, depth + 1))
                    elif child.is_file():
                        result = self.push_file(child, target, progress, conflict, resume)
                        total += result["bytes"]
                        skipped += result.get("skipped", 0)
                        files += 0 if result.get("status") == "skipped" else 1
                    else:
                        raise ProtocolError("Only regular files and directories can be uploaded")
            return {"type": "directory", "files": files, "bytes": total, "skipped": skipped, "remote": remote}
        return self.push_file(source, remote, progress, conflict, resume)

    def push_file(self, source, remote, progress=None, conflict="error", resume=False):
        self.require_security()
        self.check_cancel()
        if resume and self.version >= 4:
            from .resume import upload
            return upload(self, source, remote, progress, conflict)
        options = self.batch_options()
        frames = options.get("batch_frames", 0)
        original = remote
        remote = self.remote_destination(remote, conflict)
        if remote is None:
            return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "remote": original}
        active = False
        try:
            with Path(source).open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                self.send_json(PUSH, {"path": safe_path(remote), "size": size, **options})
                ready = self.json_response(META)
                active = True
                if ready.get("ready") is not True:
                    raise ProtocolError("Phone did not approve upload")
                self.verify_batch_mode(ready, frames)
                total, digest = 0, hashlib.sha256()
                if progress:
                    progress(remote, 0, size)
                while True:
                    if self.cancelled():
                        self.cancel_at_boundary()
                        active = False
                        raise TransferCancelled("Cancelled; incomplete upload removed by phone")
                    chunk = stream.read(MAX_FRAME * (frames or 1))
                    if not chunk:
                        break
                    self.send_upload_batch(chunk, total + len(chunk), frames)
                    total += len(chunk)
                    digest.update(chunk)
                    if progress:
                        progress(remote, total, size)
                if self.cancelled():
                    self.cancel_at_boundary()
                    active = False
                    raise TransferCancelled("Cancelled; incomplete upload removed by phone")
                if total != size:
                    raise ProtocolError("Local file size changed during upload")
                self.send_json(END, {"bytes": total, "sha256": digest.hexdigest()})
                active = False
                result = self.json_response(END)
                check_digest(result, total, digest.hexdigest())
                if progress:
                    progress(remote, total, total)
                return {**result, "type": "file", "remote": remote}
        except BaseException:
            if active:
                try:
                    self.cancel_at_boundary()
                except Exception:
                    pass
            raise
