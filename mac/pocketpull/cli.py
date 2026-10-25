import argparse
import json
import signal
import threading
from pathlib import Path
import subprocess
import sys

from .discovery import candidates, describe, label, device_record, identify_accessory, record_label
from .protocol import Connection, TransferCancelled, joined
from .progress import Progress
from .usb_lock import USBLease
from .security import PairingRequired
from .usb_transport import USBTransport, choose, devices, is_accessory, switch


def number(value):
    return int(value, 0)


def parser():
    result = argparse.ArgumentParser(prog="pocket-dimension", description="Pocket Dimension — selected Android files over USB, without ADB")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("gui", help="Open the native macOS push/pull application")
    local = sub.add_parser("local-plan", help="Calculate local upload size (used by the GUI)")
    local.add_argument("source")
    listing = sub.add_parser("devices", help="Find recognized Android phones and show their names")
    listing.add_argument("--json", action="store_true", help="Print machine-readable devices")
    listing.add_argument("--details", action="store_true", help="Read phone model and app ID from available AOA apps without switching modes")
    listing.add_argument("--all", action="store_true", help="Also list unrecognized USB peripherals")
    for command in ("connect", "probe", "status", "pull", "ls", "push", "plan", "stat", "pair", "unpair"):
        descriptions = {
            "connect": "Find your phone, start AOA if needed, and connect to Pocket Dimension",
            "probe": "Test AOA USB endpoints without requiring the phone app",
            "status": "Connect and show the phone's approved file",
            "pull": "Download an approved file or directory with checksum verification",
            "ls": "Browse the shared folder or device storage",
            "push": "Upload a file or directory into the phone shared folder",
            "plan": "Calculate bytes and files in the shared selection",
            "stat": "Show metadata for a shared file or folder",
            "pair": "Pair after comparing fingerprints on the phone and laptop",
            "unpair": "Revoke this computer on the connected phone",
        }
        cmd = sub.add_parser(command, help=descriptions[command])
        cmd.add_argument("--vid", type=number, help="Override phone vendor ID, e.g. 0x04e8")
        cmd.add_argument("--pid", type=number, help="Override phone product ID")
        cmd.add_argument("--bus", type=int, help="Select a USB bus when multiple phones are connected")
        cmd.add_argument("--address", type=int, help="Select a USB address when multiple phones are connected")
        cmd.add_argument("--switch", action="store_true", help="Allow AOA mode switching for an explicit VID/PID")
        cmd.add_argument("--device-id", help=argparse.SUPPRESS)
        cmd.add_argument("--scope-id", help=argparse.SUPPRESS)
        cmd.add_argument("--timeout", type=int, default=30, help="USB inactivity timeout in seconds (default 30)")
        if command in ("ls", "pull", "push", "plan", "stat", "pair", "unpair"):
            cmd.add_argument("--remote", default=None, help="Relative path inside the phone sharing scope")
        if command in ("ls", "pull", "push", "connect", "plan", "stat", "pair", "unpair"):
            cmd.add_argument("--json", action="store_true", help="Print machine-readable results")
        if command in ("pull", "push"):
            cmd.add_argument("--no-batching", action="store_true", help="Use per-frame acknowledgments for comparison or troubleshooting")
            cmd.add_argument("--resume", action="store_true", help="Retain and prefix-verify partial data before resuming (APK v0.5+)")
            cmd.add_argument("--conflict", choices=("error", "skip", "keep-both"), default="error", help="Existing destination: stop, skip, or choose a new name")
            cmd.add_argument("--no-progress", action="store_true")
            cmd.add_argument("--json-progress", action="store_true", help="Emit JSON progress records on stderr")
        if command == "pair":
            cmd.add_argument("--phone-fingerprint", required=True, help="Full phone fingerprint verified on its physical screen")
        if command == "pull":
            cmd.add_argument("destination", help="New local file or directory; must not exist")
        if command == "push":
            cmd.add_argument("source", help="Local file or directory to upload; no overwrites")
    return result


def main(argv=None):
    argparser = parser()
    arguments = sys.argv[1:] if argv is None else argv
    args = argparser.parse_args(arguments or ["connect"])
    if args.command == "local-plan":
        try:
            source = Path(args.source)
            if source.is_symlink(): raise ValueError("Symbolic links are not supported")
            files = 0; total = 0; visited = 0
            pending = [(source, 0)]
            while pending:
                path, depth = pending.pop()
                visited += 1
                if visited > 100000 or depth > 128: raise ValueError("Directory traversal limit exceeded")
                if path.is_symlink(): raise ValueError("Symbolic links are not supported")
                if path.is_dir(): pending.extend((child, depth + 1) for child in path.iterdir())
                elif path.is_file(): total += path.stat().st_size; files += 1
                else: raise ValueError("Only regular files and directories are supported")
                if files + len(pending) > 100000: raise ValueError("Too many files")
            print(json.dumps({"bytes": total, "files": files, "known": True})); return 0
        except (OSError, ValueError) as error:
            print(f"Pocket Dimension: {error}", file=sys.stderr); return 1
    if args.command == "gui":
        location = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "dist"
        app = location / "Pocket Dimension.app"
        if not app.exists():
            legacy = location / "PocketPull.app"
            if legacy.exists():
                app = legacy
        if not app.exists():
            print("Pocket Dimension: build the GUI first with ./scripts/build-gui.sh, or keep Pocket Dimension.app beside the CLI.", file=sys.stderr)
            return 1
        try:
            subprocess.run(["open", str(app)], check=True)
            return 0
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"Pocket Dimension: {error}", file=sys.stderr)
            return 1
    if args.command != "devices":
        if (args.vid is None) != (args.pid is None):
            argparser.error("--vid and --pid must be supplied together")
        if args.switch and args.vid is None:
            argparser.error("--switch requires explicit --vid and --pid")
        if args.timeout <= 0:
            argparser.error("--timeout must be positive")
    transport = None
    connection = None
    lease = None
    progress = None
    cancel_requested = threading.Event()
    previous_handlers = {}
    if args.command in ("pull", "push", "plan"):
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.signal(sig, lambda *_: cancel_requested.set())
    try:
        found = devices()
        if args.command == "devices":
            shown = [dev for dev in found if args.all or label(dev)]
            records = [device_record(dev, identify_accessory(dev) if args.details else None) for dev in shown]
            if args.json:
                print(json.dumps(records, ensure_ascii=True))
                return 0
            for record in records:
                print(f"{record['vid']:04x}:{record['pid']:04x}  bus={record['bus']} address={record['address']}  {record_label(record)}")
            if not shown:
                print("No recognized Android phones. Unlock your phone and select File transfer in its USB settings.\n"
                      "Use pocket-dimension devices --all to find an unrecognized phone.")
            return 0
        matches = candidates(found, args.vid, args.pid, args.bus, args.address)
        if len(matches) > 1:
            raise RuntimeError("Multiple phones match. Select one with --bus N --address N:\n"
                               + "\n".join(describe(dev) for dev in matches))
        device = choose(matches)
        if not is_accessory(device):
            automatic = args.command != "probe" and label(device) is not None
            if not (args.switch or automatic or args.command == "connect"):
                raise RuntimeError("Phone is not in AOA mode. Run pocket-dimension connect, or use probe --vid … --pid … --switch.")
            print(f"Connecting to {describe(device)}…\n"
                  "Unlock the phone and approve the Pocket Dimension accessory prompt.", file=sys.stderr)
            device = switch(device)
        lease = USBLease(device, timeout=min(args.timeout, 15), cancelled=cancel_requested.is_set)
        transport = USBTransport(device, max(args.timeout, 90 if args.command == "pair" else args.timeout) * 1000)
        if args.command == "probe":
            print("AOA connection established; interface 0 bulk endpoints claimed successfully.")
            return 0
        connection = Connection(transport)
        connection.batching_enabled = not getattr(args, "no_batching", False)
        connection.cancelled = cancel_requested.is_set
        connection.hello(pair_fingerprint=args.phone_fingerprint if args.command == "pair" else None, revoke=args.command == "unpair")
        if args.command == "unpair":
            print("Computer trust revoked on the phone."); return 0
        if args.command == "pair":
            print("Computer paired for seven days. Choose which files to share on the phone."); return 0
        if args.device_id and connection.device_id != args.device_id:
            raise RuntimeError("This queued transfer belongs to a different phone. Connect the original device before retrying.")
        if args.scope_id and connection.selected().get("scope_id") != args.scope_id:
            raise RuntimeError("The phone sharing scope changed. Select the original folder and reconnect before retrying.")
        if args.command == "connect":
            selection = connection.selected()
            if args.json:
                print(json.dumps({**selection, "device_id": connection.device_id, "device": device_record(device, {**connection.device_info, "device_id": connection.device_id})}, ensure_ascii=True))
            else:
                print("Connected to Pocket Dimension.")
                if selection.get("selected"):
                    print("Shared: " + json.dumps(selection.get("name"), ensure_ascii=True))
                    print("Run: pocket-dimension ls   or   pocket-dimension pull ./your-filename")
                else:
                    print("Tap Choose a file or Choose a folder in the phone app.")
        elif args.command == "status":
            print(json.dumps(connection.selected(), indent=2, ensure_ascii=True))
        elif args.command == "stat":
            print(json.dumps(connection.stat(args.remote or ""), ensure_ascii=True))
        elif args.command == "plan":
            print(json.dumps(connection.plan(args.remote or "")))
        elif args.command == "ls":
            path = args.remote or ""
            info = connection.stat(path)
            if info.get("type") == "directory":
                entries = [{**entry, "path": joined(path, entry["name"])} for entry in connection.browse(path)]
            else:
                entries = [{**info, "path": path}]
            if args.json:
                print(json.dumps(entries, ensure_ascii=True))
            else:
                for entry in entries:
                    kind = "dir " if entry["type"] == "directory" else "file"
                    size = "—" if entry.get("size") is None else str(entry["size"])
                    print(f"{kind}  {size:>12}  {json.dumps(entry['name'], ensure_ascii=True)}")
        else:
            if args.resume and connection.version < 4:
                print("Pocket Dimension: update the APK for resume; transferring from the beginning.", file=sys.stderr)
            progress = Progress(not args.no_progress, args.json_progress)
            if args.command == "pull":
                result = connection.pull(args.destination, remote=args.remote or "", progress=progress, conflict=args.conflict, resume=args.resume)
                action = f"Saved to {args.destination}"
            else:
                result = connection.push(args.source, remote=args.remote, progress=progress, conflict=args.conflict, resume=args.resume)
                action = "Uploaded to the phone"
            progress.finish()
            if args.json:
                print(json.dumps(result, ensure_ascii=True))
            elif result.get("status") == "skipped":
                print("Skipped existing destination.")
            elif result.get("type") == "directory":
                print(f"{action}: {result['files']} files, {result['bytes']} bytes (each checksum verified)")
            else:
                print(f"{action}: {result['bytes']} bytes\nSHA-256 {result['sha256']} (verified)")
        return 0
    except PairingRequired as exc:
        if 'device' in locals(): exc.details['device'] = device_record(device, {'device_id': getattr(connection, 'device_id', None), **getattr(connection, 'device_info', {})})
        print(json.dumps(exc.details,ensure_ascii=True),file=sys.stderr)
        print("Pocket Dimension: " + str(exc),file=sys.stderr)
        return 3
    except TransferCancelled as exc:
        print(f"Pocket Dimension: {exc}", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Pocket Dimension: {exc}", file=sys.stderr)
        return 1
    finally:
        if progress:
            progress.finish()
        if connection:
            try: connection.close()
            except Exception as exc: print(f"Pocket Dimension: secure session cleanup: {exc}", file=sys.stderr)
        if transport:
            try:
                transport.close()
            except Exception as exc:
                print(f"Pocket Dimension: USB cleanup: {exc}", file=sys.stderr)
        if lease:
            lease.close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
