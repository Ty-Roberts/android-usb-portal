import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from pocketpull.security import HostSecurity,Identity,PairingRequired,SecureTransport,SecurityError
from pocketpull.protocol import Connection,ProtocolError

ROOT=Path(__file__).resolve().parents[1]
JAVA_HOME=Path('/Applications/Android Studio.app/Contents/jbr/Contents/Home')
ANDROID_JAR=Path.home()/'Library/Android/sdk/platforms/android-36/android.jar'

class MemoryStore:
    def __init__(self,identity):
        self.value={'version':1,'certificate':base64.b64encode(identity.certificate_pem).decode(),'key':base64.b64encode(identity.private_key_pem).decode(),'phones':{}}
    def load(self):return json.loads(json.dumps(self.value))
    def update_pin(self,fp,value):
        if value is None:self.value['phones'].pop(fp,None)
        else:self.value['phones'][fp]=value

class PipeTransport:
    def __init__(self,command):
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.deadline=threading.Timer(12,self.process.kill);self.deadline.start()
        self.sent=bytearray();self.received=bytearray();self.corrupt=False
    def read(self):
        header=self.process.stdout.read(5)
        if len(header)!=5:return b''
        length=struct.unpack('>I',header[1:])[0]
        if length>16379:raise AssertionError('Oversized outer record')
        payload=self.process.stdout.read(length)
        result=header+payload;self.received.extend(result);return result
    def write(self,data):
        if self.corrupt:
            data=data[:-1]+bytes([data[-1]^1]);self.corrupt=False
        self.sent.extend(data);self.process.stdin.write(data);self.process.stdin.flush()
    def close(self):
        self.deadline.cancel()
        if self.process.poll() is None:self.process.terminate()
        self.process.communicate(timeout=3)

class SecureUSBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.client=Identity.generate();cls.server=Identity.generate()
        key=serialization.load_pem_private_key(cls.server.private_key_pem,password=None)
        (cls.root/'server.p12').write_bytes(pkcs12.serialize_key_and_certificates(b'fixture',key,cls.server.certificate,None,serialization.BestAvailableEncryption(b'fixture')))
        cls.json=ROOT/'build/security-tests/json.jar'
        if not cls.json.exists():raise RuntimeError('Run scripts/test-security.sh to fetch the JVM test-only JSON jar')
        cls.classpath=os.pathsep.join(map(str,[cls.root,cls.json,ANDROID_JAR]))
        files=list((ROOT/'usb-android/src/main/java/dev/pocketdimension/usb').glob('*.java'))+[ROOT/'tests/security/SecurityHarness.java']
        subprocess.run([str(JAVA_HOME/'bin/javac'),'-cp',os.pathsep.join(map(str,[cls.json,ANDROID_JAR])),'-d',str(cls.root),*map(str,files)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def transport(self,mode='trusted'):
        raw=PipeTransport([str(JAVA_HOME/'bin/java'),'-cp',self.classpath,'dev.pocketdimension.usb.SecurityHarness',mode,str(self.root/'server.p12'),self.client.fingerprint])
        self.addCleanup(raw.close);return raw
    def owner(self,known=True):
        store=MemoryStore(self.client)
        if known:store.update_pin(self.server.fingerprint,{'expires_at':1})
        return HostSecurity(store),store
    def test_phone_authoritative_policy(self):
        result=subprocess.run([str(JAVA_HOME/'bin/java'),'-cp',self.classpath,'dev.pocketdimension.usb.SecurityHarness','policy'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('PASS fixed expiry',result.stdout)
    def test_actual_java_library_and_python_mutual_tls_roundtrip(self):
        raw=self.transport();owner,_=self.owner();channel=owner.connect(raw)
        self.addCleanup(lambda:channel.close())
        secret='sensitive-file-猫'.encode()*4000
        channel.write(struct.pack('>I',len(secret))+secret)
        returned=channel._exact_plain(4);size=struct.unpack('>I',returned)[0]
        self.assertEqual(channel._exact_plain(size),secret)
        self.assertNotIn(secret[:64],raw.sent);self.assertNotIn(secret[:64],raw.received)
        self.assertEqual(channel.tls.version(),'TLSv1.3')
    def test_unknown_phone_does_not_receive_application_data(self):
        raw=self.transport();owner,store=self.owner(False)
        with self.assertRaises(PairingRequired)as caught:owner.connect(raw)
        self.assertEqual(caught.exception.details['phone_fingerprint'],self.server.fingerprint)
        self.assertEqual(store.value['phones'],{})
    def test_pair_requires_explicit_correct_phone_fingerprint(self):
        raw=self.transport('pair');owner,store=self.owner(False)
        with self.assertRaises(SecurityError):owner.connect(raw,mode='pair',expected_phone='0'*64)
        self.assertEqual(store.value['phones'],{})
    def test_pair_approval_and_confirmation_store_pin(self):
        raw=self.transport('pair');owner,store=self.owner(False)
        channel=owner.connect(raw,mode='pair',expected_phone=self.server.fingerprint)
        self.assertIn(self.server.fingerprint,store.value['phones']);channel.close()
    def test_denied_pair_does_not_store_pin(self):
        raw=self.transport('deny');owner,store=self.owner(False)
        with self.assertRaises(PairingRequired):owner.connect(raw,mode='pair',expected_phone=self.server.fingerprint)
        self.assertEqual(store.value['phones'],{})
    def test_expired_or_revoked_host_requires_pairing(self):
        raw=self.transport('unknown');owner,_=self.owner()
        with self.assertRaises(PairingRequired):owner.connect(raw)
    def test_expiry_blocks_existing_application_streams(self):
        raw=self.transport('expire');owner,_=self.owner();channel=owner.connect(raw)
        channel.write(struct.pack('>I',6)+b'secret')
        with self.assertRaises((SecurityError,ssl.SSLError,EOFError)):channel._exact_plain(4)
    def test_tampered_encrypted_record_is_rejected(self):
        raw=self.transport();owner,_=self.owner();channel=owner.connect(raw)
        raw.corrupt=True;channel.write(struct.pack('>I',6)+b'secret')
        with self.assertRaises((SecurityError,ssl.SSLError,EOFError)):channel._exact_plain(4)
    def test_missing_client_certificate_is_rejected(self):
        raw=self.transport();context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
        with self.assertRaises((SecurityError,ssl.SSLError,EOFError,BrokenPipeError)):
            channel=SecureTransport(raw,context);channel.write_control({'mode':'connect'});channel.read_control()
    def test_plaintext_downgrade_and_unestablished_access_are_rejected(self):
        from test_protocol import Transport,frame
        with self.assertRaisesRegex(ProtocolError,'authenticated encryption'):Connection(Transport(frame(1,{'version':4}))).hello()
        with self.assertRaisesRegex(ProtocolError,'authenticated encrypted'):Connection(Transport(b'')).selected()
    def test_unpair_revokes_the_phone_record_and_local_pin(self):
        raw=self.transport();owner,store=self.owner();self.assertIsNone(owner.connect(raw,mode='revoke'))
        self.assertNotIn(self.server.fingerprint,store.value['phones'])

    def test_tls12_fallback_is_aead_and_authorized(self):
        raw=self.transport('tls12');owner,_=self.owner();channel=owner.connect(raw)
        self.assertEqual(channel.tls.version(),'TLSv1.2')
        self.assertIn('GCM',channel.tls.cipher()[0]);channel.close()

    def test_locked_phone_rejects_even_a_trusted_computer(self):
        raw=self.transport('locked');owner,_=self.owner()
        with self.assertRaises((SecurityError,ssl.SSLError,EOFError)):owner.connect(raw)

    def test_small_fragmentation_preserves_tls_and_file_bytes(self):
        raw=self.transport();original=raw.write
        def fragmented(data):
            kind,length=struct.unpack('>BI',data[:5]);payload=data[5:]
            self.assertEqual(length,len(payload))
            for offset in range(0,len(payload),97):
                part=payload[offset:offset+97];original(struct.pack('>BI',kind,len(part))+part)
        raw.write=fragmented
        owner,_=self.owner();channel=owner.connect(raw)
        secret=os.urandom(8000);channel.write(struct.pack('>I',len(secret))+secret)
        self.assertEqual(struct.unpack('>I',channel._exact_plain(4))[0],len(secret))
        self.assertEqual(channel._exact_plain(len(secret)),secret);channel.close()

    def test_pending_pair_cannot_complete_after_lifecycle_invalidation(self):
        raw=self.transport('cancel_pair');owner,store=self.owner(False)
        with self.assertRaises((SecurityError,ssl.SSLError,EOFError,BrokenPipeError)):
            owner.connect(raw,mode='pair',expected_phone=self.server.fingerprint)
        self.assertEqual(store.value['phones'],{})

    def test_inspection_then_pairing_reuses_same_physical_transport(self):
        raw=self.transport('reuse');owner,store=self.owner(False)
        with self.assertRaises(PairingRequired):owner.connect(raw)
        channel=owner.connect(raw,mode='pair',expected_phone=self.server.fingerprint)
        channel.write(struct.pack('>I',5)+b'hello')
        self.assertEqual(channel._exact_plain(9),struct.pack('>I',5)+b'hello');channel.close()

    def test_keychain_denial_does_not_start_phone_tls_deadline(self):
        from unittest.mock import patch
        from test_protocol import Transport,frame
        raw=Transport(frame(1,{'version':5}))
        with patch('pocketpull.security.HostSecurity',side_effect=SecurityError('Keychain denied')):
            with self.assertRaisesRegex(SecurityError,'Keychain denied'):Connection(raw).hello()
        self.assertEqual(raw.writes,[frame(1)])
