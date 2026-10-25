import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pocketpull.protocol import (Connection, ProtocolError, TransferCancelled, BATCH_FRAMES, MAX_FRAME,
                                HELLO, GET, PUSH, META, DATA, END, CANCEL, ERROR)
from test_resume import ResumePhone


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class BatchPhone(ResumePhone):
    def __init__(self):
        super().__init__()
        self.window = 0
        self.pending = []
        self.upload_acks = self.download_acks = 0
        self.bad_boundary = False
        self.bad_digest = False

    def next_batch(self):
        path, content, offset = self.download
        if offset == len(content):
            checksum = '0'*64 if self.bad_digest else hashlib.sha256(content).hexdigest()
            self.reply(END, {'bytes': len(content), 'sha256': checksum})
            self.download = None
            return
        for _ in range(self.window):
            chunk = content[offset:offset+MAX_FRAME]
            if not chunk: break
            self.reply(DATA, chunk); offset += len(chunk)
        self.download = (path, content, offset)
        self.reply(META, {'batch': True, 'bytes': offset + (1 if self.bad_boundary else 0)})

    def write(self, data):
        kind = data[0]
        request = json.loads(data[5:]) if data[5:] and kind != DATA else {}
        if kind == HELLO:
            self.reply(HELLO, {'version': 4, 'device_id': 'phone', 'features': ['resume', 'batch-ack']})
        elif kind == GET and request.get('batch_frames'):
            self.window = request['batch_frames']
            path = request['path']; content = self.tree[path]; offset = request.get('offset', 0)
            if request.get('prefix_sha256', hashlib.sha256(b'').hexdigest()) != hashlib.sha256(content[:offset]).hexdigest():
                self.reply(ERROR, b'RESUME_MISMATCH: changed prefix'); return
            self.reply(META, {**self.metadata(path), 'offset': offset, 'batch_frames': self.window})
            self.download = (path, content, offset); self.next_batch()
        elif kind == META and self.download is not None and self.window:
            assert request['bytes'] == self.download[2]
            self.download_acks += 1; self.next_batch()
        elif kind == PUSH and request.get('batch_frames'):
            self.window = request['batch_frames']; self.pending=[]
            key = (request['path'], request.get('sha256')) if request.get('resume') else None
            content = self.parts.setdefault(key, bytearray()) if key else bytearray()
            self.active = (request['path'], request['size'], content); self.active_key = key
            self.reply(META, {'ready': True, 'offset': len(content), 'prefix_sha256': hashlib.sha256(content).hexdigest(), 'batch_frames': self.window})
        elif kind == DATA and self.active is not None and self.window:
            assert len(self.pending) < self.window
            self.pending.append(data[5:])
        elif kind == META and self.active is not None and self.window:
            path, size, content = self.active
            batch=b''.join(self.pending); self.pending=[]
            assert request['batch'] and request['bytes'] == len(content)+len(batch)
            if len(content)+len(batch)>size:
                self.active=None;self.reply(ERROR,b'Upload exceeds declared size');return
            content.extend(batch);self.upload_acks+=1
            self.reply(META, {'bytes':len(content)})
        elif kind == CANCEL:
            self.pending=[];super().write(data)
        else:
            if kind in (GET,PUSH): self.window=0
            super().write(data)


class BatchingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        env=patch.dict(os.environ,{'POCKET_DIMENSION_CACHE':str(self.root/'cache')});env.start();self.addCleanup(env.stop)
        self.phone=BatchPhone();self.cancel=False
        self.payload=bytes(range(251))*4000+b'end'
        self.phone.tree['docs/photo.bin']=self.payload
        self.connection=LegacyConnection(self.phone,cancelled=lambda:self.cancel);self.connection.hello()

    def trigger_cancel(self,name,count,total):
        if count:self.cancel=True

    def test_download_full_and_partial_batches_with_and_without_resume(self):
        for resume in (False,True):
            with self.subTest(resume=resume):
                before=self.phone.download_acks
                target=self.root/str(resume)
                self.connection.pull(target,'docs/photo.bin',resume=resume)
                self.assertEqual(target.read_bytes(),self.payload)
                self.assertEqual(self.phone.download_acks-before,math.ceil(len(self.payload)/(MAX_FRAME*BATCH_FRAMES)))

    def test_upload_full_and_partial_batches_with_and_without_resume(self):
        source=self.root/'source';source.write_bytes(self.payload)
        for resume in (False,True):
            with self.subTest(resume=resume):
                before=self.phone.upload_acks
                self.connection.push(source,'uploaded-'+str(resume),resume=resume)
                self.assertEqual(self.phone.tree['uploaded-'+str(resume)],self.payload)
                self.assertEqual(self.phone.upload_acks-before,math.ceil(len(self.payload)/(MAX_FRAME*BATCH_FRAMES)))

    def test_download_cancel_confirms_boundary_and_resumes(self):
        target=self.root/'download'
        with self.assertRaises(TransferCancelled): self.connection.pull(target,'docs/photo.bin',self.trigger_cancel,resume=True)
        self.assertFalse(target.exists());self.assertIsNone(self.phone.download)
        self.cancel=False
        self.assertTrue(self.connection.selected()['selected'])
        result=self.connection.pull(target,'docs/photo.bin',resume=True)
        self.assertEqual(result['resumed_bytes'],MAX_FRAME*BATCH_FRAMES)
        self.assertEqual(target.read_bytes(),self.payload)

    def test_nonresume_cancel_removes_output_in_both_directions(self):
        target=self.root/'download'
        with self.assertRaises(TransferCancelled):self.connection.pull(target,'docs/photo.bin',self.trigger_cancel)
        self.assertFalse(target.exists());self.cancel=False
        source=self.root/'source';source.write_bytes(self.payload)
        with self.assertRaises(TransferCancelled):self.connection.push(source,'uploaded',self.trigger_cancel)
        self.assertNotIn('uploaded',self.phone.tree);self.assertIsNone(self.phone.active)
        self.cancel=False;self.assertTrue(self.connection.selected()['selected'])

    def test_upload_cancel_retains_acknowledged_prefix_and_resumes(self):
        source=self.root/'source';source.write_bytes(self.payload)
        with self.assertRaises(TransferCancelled):self.connection.push(source,'uploaded',self.trigger_cancel,resume=True)
        self.assertNotIn('uploaded',self.phone.tree);self.assertFalse(self.phone.pending)
        self.cancel=False
        result=self.connection.push(source,'uploaded',resume=True)
        self.assertEqual(result['resumed_bytes'],MAX_FRAME*BATCH_FRAMES)
        self.assertEqual(self.phone.tree['uploaded'],self.payload)

    def test_bad_boundary_refuses_file_and_cancels_session_cleanly(self):
        self.phone.bad_boundary=True
        with self.assertRaisesRegex(ProtocolError,'count mismatch'):self.connection.pull(self.root/'bad','docs/photo.bin',resume=True)
        self.assertFalse((self.root/'bad').exists());self.assertIsNone(self.phone.download)
        self.assertTrue(self.connection.selected()['selected'])

    def test_final_checksum_is_still_required(self):
        self.phone.bad_digest=True
        with self.assertRaises(ProtocolError):self.connection.pull(self.root/'bad','docs/photo.bin',resume=True)
        self.assertFalse((self.root/'bad').exists())

    def test_empty_files_have_no_batch_deadlock(self):
        self.phone.tree['empty']=b''
        target=self.root/'empty';self.connection.pull(target,'empty',resume=True)
        self.assertEqual(target.read_bytes(),b'')
        self.connection.push(target,'uploaded-empty',resume=True)
        self.assertEqual(self.phone.tree['uploaded-empty'],b'')

    def test_unaligned_resume_prefix(self):
        source=self.root/'source';source.write_bytes(self.payload)
        checksum=hashlib.sha256(self.payload).hexdigest()
        self.phone.parts[('uploaded',checksum)]=bytearray(self.payload[:12345])
        result=self.connection.push(source,'uploaded',resume=True)
        self.assertEqual(result['resumed_bytes'],12345)
        self.assertEqual(self.phone.tree['uploaded'],self.payload)

    def test_old_or_explicitly_disabled_peers_keep_per_frame_mode(self):
        self.connection.batching_enabled=False
        target=self.root/'disabled';self.connection.pull(target,'docs/photo.bin',resume=True)
        self.assertEqual(target.read_bytes(),self.payload);self.assertEqual(self.phone.window,0)
        self.connection.features=set();self.connection.batching_enabled=True
        self.assertEqual(self.connection.batch_options(),{})

    def test_oversized_window_and_missing_boundary_are_rejected(self):
        from test_protocol import Transport,frame
        connection=LegacyConnection(Transport(b''.join(frame(DATA,b'x') for _ in range(BATCH_FRAMES+1))))
        with self.assertRaisesRegex(ProtocolError,'bounded'):connection.receive_download_batch(BATCH_FRAMES)
        connection=LegacyConnection(Transport(frame(DATA,b'x')+frame(END,{'bytes':1})))
        with self.assertRaisesRegex(ProtocolError,'boundary'):connection.receive_download_batch(BATCH_FRAMES)
