"""Opt-in: run only while StorageChecks -e usb true exposes its generated fixture."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from pocketpull.discovery import candidates
from pocketpull.protocol import Connection, TransferCancelled, RemoteError
from pocketpull.usb_lock import USBLease
from pocketpull.usb_transport import USBTransport, choose, devices, is_accessory, switch


def main():
    results = []
    def check(name, operation):
        started = time.monotonic(); operation()
        results.append({'check': name, 'passed': True, 'seconds': round(time.monotonic() - started, 2)})
        print('PASS ' + name, flush=True)
    def require(value):
        if not value: raise AssertionError('Acceptance assertion failed')
    device = choose(candidates(devices()))
    if not is_accessory(device): device = switch(device)
    lease = USBLease(device, timeout=15)
    transport = USBTransport(device, 30000)
    phone = Connection(transport); phone.hello()
    scope = phone.selected()
    if not scope.get('name', '').startswith('PocketDimension-USB-check-'):
        raise RuntimeError('Refusing to test outside the generated USB fixture')
    with tempfile.TemporaryDirectory(prefix='PocketDimension-USB-') as temporary:
        root = Path(temporary); os.environ['POCKET_DIMENSION_CACHE'] = str(root / 'cache')
        data = bytes(i % 251 for i in range(65536)) * 192
        checksum = hashlib.sha256(data).hexdigest()
        check('encrypted protocol v5 and scoped identity', lambda: require(phone.version == 5 and phone.secure_transport is not None and phone.device_id and scope.get('scope_id')))
        def tree():
            result = phone.pull(root/'tree', remote='', resume=True)
            require(result['files'] == 2 and (root/'tree/large.bin').read_bytes() == data and (root/'tree/nested/empty').stat().st_size == 0)
        check('recursive download and empty file checksum', tree)
        def cancel_download():
            flag = [False]; phone.cancelled = lambda: flag[0]
            def progress(name, count, total):
                if count >= 131072: flag[0] = True
            try: phone.pull(root/'resumed.bin', remote='large.bin', progress=progress, resume=True)
            except TransferCancelled: pass
            else: raise AssertionError('Did not cancel')
            require(not (root/'resumed.bin').exists())
            phone.cancelled = lambda: False
            require(phone.selected()['scope_id'] == scope['scope_id'])
            result = phone.pull(root/'resumed.bin', remote='large.bin', resume=True)
            require(result['resumed_bytes'] >= 131072 and result['sha256'] == checksum and (root/'resumed.bin').read_bytes() == data)
        check('cancel download, reuse session, prefix-verified resume', cancel_download)
        source = root/'upload.bin'; source.write_bytes(data)
        def cancel_upload():
            flag = [False]; phone.cancelled = lambda: flag[0]
            def progress(name, count, total):
                if count >= 131072: flag[0] = True
            try: phone.push(source, remote='upload.bin', progress=progress, resume=True)
            except TransferCancelled: pass
            else: raise AssertionError('Did not cancel')
            phone.cancelled = lambda: False
            require(not any(item['name'] == 'upload.bin' for item in phone.browse('')))
            result = phone.push(source, remote='upload.bin', resume=True)
            require(result['resumed_bytes'] >= 131072 and result['sha256'] == checksum)
            phone.pull(root/'verify-upload', remote='upload.bin', resume=True)
            require((root/'verify-upload').read_bytes() == data)
        check('cancel upload, private prefix, resume and readback', cancel_upload)
        def conflicts():
            require(phone.push(source, remote='upload.bin', conflict='skip', resume=True)['status'] == 'skipped')
            result = phone.push(source, remote='upload.bin', conflict='keep-both', resume=True)
            require(result['remote'] != 'upload.bin')
            try: phone.push(source, remote='upload.bin', resume=True)
            except (FileExistsError, RemoteError): pass
            else: raise AssertionError('Overwrite accepted')
            require(phone.pull(root/'resumed.bin', remote='large.bin', conflict='skip', resume=True)['status'] == 'skipped')
            require(phone.pull(root/'resumed.bin', remote='large.bin', conflict='keep-both', resume=True)['destination'] != str(root/'resumed.bin'))
        check('Skip Keep Both and no-overwrite policies', conflicts)
        def upload_tree():
            directory = root/'outgoing'; (directory/'sub').mkdir(parents=True)
            (directory/'sub/hello.txt').write_text('Pocket Dimension fixture\n'); (directory/'empty').touch()
            result = phone.push(directory, remote='uploaded-tree', resume=True)
            require(result['files'] == 2)
            phone.pull(root/'returned-tree', remote='uploaded-tree', resume=True)
            require((root/'returned-tree/sub/hello.txt').read_text() == 'Pocket Dimension fixture\n')
        check('recursive upload and download roundtrip', upload_tree)
        check('byte planning and modification timestamps', lambda: require(phone.plan('large.bin')['bytes'] == len(data) and phone.stat('large.bin')['modified'] > 0))
        phone.mkdir('__finish__')
    try: phone.close()
    except Exception: pass  # Fixture teardown may already close the accessory.
    transport.close(); lease.close()
    Path('build/phone-acceptance.json').write_text(json.dumps({'device': phone.device_id, 'scope': scope['name'], 'batch_frames': phone.batch_options().get('batch_frames', 0), 'checks': results}, indent=2) + '\n')


if __name__ == '__main__': main()
