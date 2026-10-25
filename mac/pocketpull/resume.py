"""Prefix-verified, identity-bound resumable file transfers (protocol v4)."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time


def cache_root():
    root = Path(os.environ.get("POCKET_DIMENSION_CACHE", str(Path.home() / "Library/Caches/PocketDimension"))) / "resume"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise OSError("Resume cache must be an owned directory, not a symbolic link")
    for entry in root.iterdir():
        if len(entry.stem) == 64 and all(c in "0123456789abcdef" for c in entry.stem) and entry.suffix in (".part", ".json"):
            try:
                if not entry.is_symlink() and time.time() - entry.stat().st_mtime > 7 * 86400: entry.unlink()
            except FileNotFoundError: pass
    return root


def hash_stream(stream, count=None, cancelled=None):
    digest = hashlib.sha256()
    remaining = count
    while remaining is None or remaining > 0:
        if cancelled and cancelled():
            from .protocol import TransferCancelled
            raise TransferCancelled("Cancelled while verifying resume data")
        chunk = stream.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
        if not chunk:
            if remaining:
                raise OSError("Resume prefix is shorter than expected")
            break
        digest.update(chunk)
        if remaining is not None:
            remaining -= len(chunk)
    return digest


def download(connection, destination, remote, progress, conflict):
    from .protocol import (GET, META, DATA, END, ProtocolError, RemoteError, TransferCancelled,
                           safe_path, local_destination, parse_object, check_digest)
    options = connection.batch_options()
    frames = options.get("batch_frames", 0)
    original = Path(destination)
    destination = local_destination(original, conflict)
    if destination is None:
        return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "destination": str(original)}
    info = connection.stat(remote)
    selection = connection.selected()
    identity = {"device": connection.device_id, "scope": selection.get("scope_id"),
                "remote": safe_path(remote), "destination": str(destination.absolute()),
                "size": info.get("size"), "modified": info.get("modified")}
    if not identity["device"] or not identity["scope"]:
        raise ProtocolError("Phone and sharing-scope identities are required for safe resume")
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = cache_root()
    partial, state = root / (key + ".part"), root / (key + ".json")
    # Never follow a planted partial file or write through a hard link.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(partial, flags, 0o600)
    attrs = os.fstat(descriptor)
    if not stat.S_ISREG(attrs.st_mode) or attrs.st_nlink != 1:
        os.close(descriptor)
        raise ProtocolError("Unsafe resume cache file")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise ProtocolError("This resumable download is already running")
    active = ack_pending = False
    complete = False
    discard = False
    try:
        with os.fdopen(descriptor, "r+b") as out:
            offset = out.seek(0, os.SEEK_END)
            if isinstance(identity["size"], int) and offset > identity["size"]:
                out.truncate(0); offset = 0
            out.seek(0)
            digest = hash_stream(out, offset, connection.cancelled)
            prefix = digest.hexdigest()
            metadata_temp = root / (key + ".json.tmp")
            metadata_temp.write_text(json.dumps({**identity, "updated": time.time()}))
            os.replace(metadata_temp, state)
            connection.send_json(GET, {"path": remote, "acknowledge": True, "offset": offset, "prefix_sha256": prefix, **options})
            try:
                metadata = connection.json_response(META)
            except RemoteError as error:
                discard = str(error).startswith("Operation stopped: RESUME_MISMATCH") or "RESUME_MISMATCH:" in str(error)
                raise
            active = True
            connection.verify_batch_mode(metadata, frames)
            if metadata.get("offset") != offset:
                raise ProtocolError("Phone did not accept the verified resume offset")
            total = offset
            expected = metadata.get("size")
            if progress:
                progress(remote or metadata.get("name", "file"), total, expected)
            while True:
                kind, payload, marker = connection.receive_download_batch(frames)
                if kind == DATA:
                    ack_pending = True
                    connection.verify_download_boundary(marker, total + len(payload))
                    if connection.cancelled():
                        connection.cancel_at_boundary(); active = ack_pending = False
                        raise TransferCancelled("Cancelled; verified download prefix retained for resume")
                    out.write(payload); digest.update(payload); total += len(payload)
                    if progress:
                        progress(remote or metadata.get("name", "file"), total, expected)
                    if connection.cancelled():
                        connection.cancel_at_boundary(); active = ack_pending = False
                        raise TransferCancelled("Cancelled; verified download prefix retained for resume")
                    connection.send_json(META, {"bytes": total}); ack_pending = False
                elif kind == END:
                    active = False
                    try:
                        check_digest(parse_object(payload), total, digest.hexdigest())
                    except ProtocolError:
                        discard = True
                        raise
                    connection.check_cancel()
                    break
                else:
                    raise ProtocolError("Unexpected resumable download frame")
            out.flush(); os.fsync(out.fileno())
            # Copy to a same-directory temporary output so final creation remains exclusive,
            # including when cache and destination are on different volumes.
            import tempfile
            import shutil
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pocket-dimension-", delete=False) as final:
                    temporary = Path(final.name); out.seek(0); shutil.copyfileobj(out, final, 1024 * 1024)
                    final.flush(); os.fsync(final.fileno())
                target = local_destination(destination, conflict)
                if target is None:
                    complete = True
                    return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "destination": str(original)}
                os.link(temporary, target); complete = True
            finally:
                if temporary is not None: temporary.unlink(missing_ok=True)
            return {"type": "file", "bytes": total, "sha256": digest.hexdigest(), "destination": str(target), "resumed_bytes": offset}
    except BaseException:
        if active and ack_pending:
            try: connection.cancel_at_boundary()
            except Exception: pass
        raise
    finally:
        if complete or discard:
            partial.unlink(missing_ok=True); state.unlink(missing_ok=True)


def upload(connection, source, remote, progress, conflict):
    from .protocol import (PUSH, META, DATA, END, MAX_FRAME, ProtocolError, TransferCancelled,
                           safe_path, check_digest)
    options = connection.batch_options()
    frames = options.get("batch_frames", 0)
    original = remote
    remote = connection.remote_destination(remote, conflict)
    if remote is None:
        return {"type": "file", "status": "skipped", "bytes": 0, "skipped": 1, "remote": original}
    active = False
    with Path(source).open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        checksum = hash_stream(stream, cancelled=connection.cancelled).hexdigest()
        stream.seek(0)
        connection.send_json(PUSH, {"path": safe_path(remote), "size": size, "resume": True, "sha256": checksum, **options})
        ready = connection.json_response(META)
        active = True
        try:
            connection.verify_batch_mode(ready, frames)
            offset = ready.get("offset")
            if ready.get("ready") is not True or type(offset) is not int or offset < 0 or offset > size:
                raise ProtocolError("Invalid upload resume offset")
            digest = hash_stream(stream, offset, connection.cancelled)
            if digest.hexdigest() != ready.get("prefix_sha256"):
                connection.send_json(12, {"discard": True})
                connection.json_response(END); active = False
                raise ProtocolError("RESUME_MISMATCH: phone prefix discarded; retry to restart safely")
            total = offset
            if progress: progress(remote, total, size)
            while True:
                if connection.cancelled():
                    connection.cancel_at_boundary(); active = False
                    raise TransferCancelled("Cancelled; upload prefix retained privately on phone for resume")
                chunk = stream.read(MAX_FRAME * (frames or 1))
                if not chunk: break
                connection.send_upload_batch(chunk, total + len(chunk), frames)
                digest.update(chunk); total += len(chunk)
                if progress: progress(remote, total, size)
            if total != size or digest.hexdigest() != checksum:
                connection.send_json(12, {"discard": True}); connection.json_response(END); active = False
                raise ProtocolError("Local source changed during upload; retained prefix discarded")
            connection.send_json(END, {"bytes": total, "sha256": checksum}); active = False
            result = connection.json_response(END)
            check_digest(result, total, checksum)
            return {**result, "type": "file", "remote": remote, "resumed_bytes": offset}
        except TransferCancelled:
            if active:
                connection.cancel_at_boundary()
            raise
        except BaseException:
            if active:
                try: connection.cancel_at_boundary()
                except Exception: pass
            raise
