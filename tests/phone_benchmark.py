"""Opt-in real USB throughput benchmark; requires the generated USBHarness scope."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import threading
import time

from pocketpull.discovery import candidates, device_record
from pocketpull.protocol import Connection
from pocketpull.usb_lock import USBLease
from pocketpull.usb_transport import USBTransport, choose, devices, is_accessory, switch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bytes', type=int, default=512 * 1024 ** 2)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--compare-batching', action='store_true', help='Compare per-frame and batched transfers with the same generated file')
    mode.add_argument('--no-batching', action='store_true', help='Compare per-frame acknowledgments on the same APK')
    parser.add_argument('--report', default='build/usb-benchmark.json')
    args = parser.parse_args()
    if args.bytes <= 0: parser.error('--bytes must be positive')
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'bytes_per_direction': args.bytes,
              'resume_enabled': True, 'transport': 'AOA USB, protocol v4', 'results': []}
    cancelled = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: cancelled.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    def save(): report_path.write_text(json.dumps(report, indent=2) + '\n')
    transport = lease = phone = None
    fixture = False
    try:
        with tempfile.TemporaryDirectory(prefix='PocketDimension-USB-benchmark-') as directory:
            root = Path(directory)
            if shutil.disk_usage(root).free < args.bytes * 4 + 1024 ** 3: raise RuntimeError('Insufficient Mac scratch space')
            os.environ['POCKET_DIMENSION_CACHE'] = str(root/'cache')
            source = root/'source.bin'; expected = hashlib.sha256()
            print(f'Generating {args.bytes:,} bytes of random test data…', flush=True)
            started = time.monotonic()
            with source.open('wb') as stream:
                remaining = args.bytes
                while remaining:
                    if cancelled.is_set(): raise InterruptedError('Cancelled while generating data')
                    chunk = os.urandom(min(4 * 1024 ** 2, remaining)); stream.write(chunk); expected.update(chunk); remaining -= len(chunk)
                stream.flush(); os.fsync(stream.fileno())
            checksum = expected.hexdigest()
            report.update(sha256=checksum, generation_seconds=round(time.monotonic()-started,3))
            print(f'Generated data in {report["generation_seconds"]:.1f}s; connecting…', flush=True)
            device = choose(candidates(devices()))
            if not is_accessory(device): device = switch(device)
            lease = USBLease(device, timeout=0, cancelled=cancelled.is_set)
            transport = USBTransport(device, 60000)
            phone = Connection(transport, cancelled=cancelled.is_set)
            phone.batching_enabled = not args.no_batching
            phone.hello()
            scope = phone.selected()
            if not scope.get('name','').startswith('PocketDimension-USB-check-'): raise RuntimeError('Refusing benchmark outside generated test scope')
            fixture = True
            if phone.version < 4: raise RuntimeError('Protocol v4 required for GUI-equivalent resume mode')
            version = dict(line.split('=', 1) for line in Path('version.properties').read_text().splitlines() if '=' in line)
            report.update(app_version=version['name'], batch_frames=phone.batch_options().get('batch_frames', 0))
            report.update(device=device_record(device, {**phone.device_info, 'device_id': phone.device_id}), scope=scope['name'], access_mode=scope.get('scope'))
            if args.compare_batching:
                if not phone.batch_options(): raise RuntimeError('Phone must advertise batching for a comparison')
                report.pop('batch_frames', None)
                report['compared_batching'] = True
            save()
            def measure(direction, operation):
                begin = time.monotonic(); progress_times = []; last_print = [begin-10]
                def progress(name, count, total):
                    now = time.monotonic()
                    if not progress_times: progress_times.append(now)
                    if count == args.bytes:
                        if len(progress_times) == 1: progress_times.append(now)
                        else: progress_times[-1] = now
                    if now-last_print[0] >= 10 or count == args.bytes:
                        seconds = now-begin
                        status = {'direction': direction, 'bytes': count, 'total': total,
                                  'elapsed_seconds': round(seconds,2), 'MB_per_second_so_far': round(count/max(seconds,.001)/1e6,2)}
                        Path('build/usb-benchmark-progress.json').write_text(json.dumps(status)+'\n')
                        print(f'{direction}: {count/args.bytes:.0%}, {count/1e9:.2f} GB, {status["MB_per_second_so_far"]:.1f} MB/s average, {seconds:.0f}s elapsed',flush=True)
                        last_print[0]=now
                result = operation(progress)
                elapsed = time.monotonic()-begin
                if result.get('bytes') != args.bytes or result.get('sha256') != checksum: raise AssertionError('Size or checksum differs')
                stream_seconds = progress_times[-1]-progress_times[0] if len(progress_times)>1 else None
                measurement = {'direction':direction, 'batch_frames':phone.batch_options().get('batch_frames', 0), 'bytes':result['bytes'], 'total_seconds':round(elapsed,3),
                    'effective_MB_per_second':round(args.bytes/elapsed/1e6,3), 'effective_MiB_per_second':round(args.bytes/elapsed/1024**2,3),
                    'stream_seconds':round(stream_seconds,3) if stream_seconds else None,
                    'stream_MB_per_second':round(args.bytes/stream_seconds/1e6,3) if stream_seconds else None,
                    'setup_and_verification_seconds':round(elapsed-(stream_seconds or elapsed),3), 'sha256_verified':True,
                    'resumed_bytes':result.get('resumed_bytes',0)}
                report['results'].append(measurement);save()
                print('COMPLETE '+json.dumps(measurement),flush=True)
            for enabled in ([False, True] if args.compare_batching else [not args.no_batching]):
                phone.batching_enabled = enabled
                tag = 'batched' if phone.batch_options() else 'per-frame'
                remote = 'benchmark-' + tag + '.bin'
                print('MODE: ' + tag, flush=True)
                measure('Mac → phone', lambda progress: phone.push(source, remote=remote, progress=progress, resume=True))
                destination=root/('download-' + tag + '.bin')
                measure('Phone → Mac', lambda progress: phone.pull(destination, remote=remote, progress=progress, resume=True))
                print('Independently hashing the downloaded file…',flush=True)
                with destination.open('rb') as stream:
                    actual=hashlib.file_digest(stream,'sha256').hexdigest()
                if actual != checksum or destination.stat().st_size != args.bytes: raise AssertionError('Independent readback differs')
                report['independent_download_hash_verified']=True
            report['completed_utc']=datetime.now(timezone.utc).isoformat();save()
            print(f'PASS: verified {args.bytes:,}-byte transfers and independent SHA-256 readback',flush=True)
    except BaseException as error:
        report['error']=f'{type(error).__name__}: {error}';save();raise
    finally:
        if phone and fixture:
            try:
                phone.cancelled=lambda:False
                phone.mkdir('__finish__')
                report['phone_cleanup_requested']=True
            except Exception as error: report['phone_cleanup_error']=str(error)
        if phone:
            try: phone.close()
            except Exception: pass
        if transport:
            try: transport.close()
            except Exception: pass
        if lease: lease.close()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        report['local_temporary_files_removed']=True
        save()


if __name__ == '__main__': main()
