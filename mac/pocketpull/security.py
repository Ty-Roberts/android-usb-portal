"""Pinned mutual TLS over USB. Bootstrap approval never authorizes file data."""
import base64
from contextlib import contextmanager
import fcntl
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

TLS_FRAME = 13
MAX_FRAGMENT = 16379
TRUST_SECONDS = 7 * 24 * 60 * 60


class SecurityError(Exception):
    pass


class PairingRequired(SecurityError):
    def __init__(self, phone_fingerprint, host_fingerprint, host_name, reason='Pair this computer on the phone before accessing files'):
        super().__init__(reason)
        self.details = {'event': 'pairing_required', 'phone_fingerprint': phone_fingerprint,
                        'host_fingerprint': host_fingerprint, 'host_name': host_name, 'reason': reason}


def normalize_fingerprint(value):
    result = value.lower().replace(':', '').replace(' ', '').replace('\n', '')
    if len(result) != 64 or any(c not in '0123456789abcdef' for c in result):
        raise SecurityError('Expected a complete SHA-256 certificate fingerprint')
    return result


class Identity:
    def __init__(self, certificate_pem, private_key_pem):
        self.certificate_pem = certificate_pem
        self.private_key_pem = private_key_pem
        self.certificate = x509.load_pem_x509_certificate(certificate_pem)
        self.fingerprint = self.certificate.fingerprint(hashes.SHA256()).hex()
        key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise SecurityError('Invalid desktop identity key')
        if key.public_key().public_numbers() != self.certificate.public_key().public_numbers():
            raise SecurityError('Desktop identity certificate/key mismatch')

    @classmethod
    def generate(cls):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Pocket Dimension computer')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                       .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
                       .not_valid_after(now+timedelta(days=3650))
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                       .sign(key, hashes.SHA256()))
        return cls(certificate.public_bytes(serialization.Encoding.PEM),
                   key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    def context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # USB has no DNS hostname or Web PKI. We explicitly pin the peer certificate
        # below, before authorization/application traffic. No automatic TOFU fallback.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers('ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384')
        # ssl requires a filename. Use owned mode-0600 files only while loading;
        # persistent secret material remains in the explicitly selected Keychain.
        with tempfile.TemporaryDirectory(prefix='PocketDimension-TLS-') as directory:
            cert = Path(directory)/'identity.pem'; key = Path(directory)/'key.pem'
            for path, data in ((cert,self.certificate_pem),(key,self.private_key_pem)):
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor,'wb') as out: out.write(data)
            context.load_cert_chain(cert, key)
        return context


class KeychainStore:
    SERVICE = 'dev.pocketdimension.secure-usb'
    def __init__(self):
        from keyring.backends.macOS import Keyring
        self.backend = Keyring()  # Never use an automatically selected/plaintext backend.
        self.account = 'identity-and-pins-v1'

    @contextmanager
    def locked(self):
        root=Path.home()/'Library/Caches/PocketDimension'
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if root.is_symlink() or root.stat().st_uid!=os.getuid():raise SecurityError('Unsafe identity lock directory')
        descriptor=os.open(root/'security.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            import stat
            attrs=os.fstat(descriptor)
            if not stat.S_ISREG(attrs.st_mode) or attrs.st_nlink!=1 or attrs.st_uid!=os.getuid():raise SecurityError('Unsafe identity lock file')
            fcntl.flock(descriptor,fcntl.LOCK_EX)
            yield
        finally:os.close(descriptor)

    def _read(self):
        raw=self.backend.get_password(self.SERVICE,self.account)
        if raw is None:return None
        value=json.loads(raw)
        if value.get('version')!=1:raise SecurityError('Unsupported Keychain identity format')
        return value

    def load(self):
        with self.locked():
            value=self._read()
            if value is None:
                identity=Identity.generate()
                value={'version':1,'certificate':base64.b64encode(identity.certificate_pem).decode(),
                       'key':base64.b64encode(identity.private_key_pem).decode(),'phones':{}}
                self.backend.set_password(self.SERVICE,self.account,json.dumps(value,sort_keys=True))
            return value

    def update_pin(self,fingerprint,value):
        with self.locked():
            state=self._read()
            if state is None:raise SecurityError('Desktop identity was removed')
            if value is None:state.setdefault('phones',{}).pop(fingerprint,None)
            else:state.setdefault('phones',{})[fingerprint]=value
            self.backend.set_password(self.SERVICE,self.account,json.dumps(state,sort_keys=True))


class HostSecurity:
    def __init__(self, store=None):
        self.store = store or KeychainStore()
        self.state = self.store.load()
        self.identity = Identity(base64.b64decode(self.state['certificate']), base64.b64decode(self.state['key']))
        self.host_name = ''.join(c for c in socket.gethostname() if c.isascii() and c.isprintable()).strip()[:80] or 'This computer'

    def connect(self, transport, *, mode='connect', expected_phone=None):
        channel = SecureTransport(transport, self.identity.context())
        try:
            actual = channel.peer_fingerprint
            known = actual in self.state.get('phones', {})
            if mode == 'pair':
                if expected_phone is None or normalize_fingerprint(expected_phone) != actual:
                    raise SecurityError('Phone certificate changed; compare the full fingerprints again')
            elif not known:
                # Inspection has no application access and never prompts on the phone.
                channel.write_control({'mode':'inspect'})
                response = channel.read_control()
                self._check_identity(response, actual)
                raise PairingRequired(actual, self.identity.fingerprint, self.host_name)
            channel.write_control({'mode':mode, 'expected_phone_fingerprint':actual, 'host_name':self.host_name})
            response = channel.read_control(); self._check_identity(response, actual)
            if response.get('status') == 'approved' and mode == 'pair':
                confirmation = response.get('confirmation')
                if not isinstance(confirmation,str) or len(confirmation)>100: raise SecurityError('Invalid pairing confirmation')
                channel.write_control({'confirmation':confirmation})
                response = channel.read_control(); self._check_identity(response, actual)
            if mode == 'revoke' and response.get('status') == 'revoked':
                self.state.get('phones',{}).pop(actual,None);self.store.update_pin(actual,None)
                channel.close();return None
            if response.get('status') != 'authorized':
                reason = 'Pairing was denied on the phone' if response.get('status')=='denied' else 'Computer trust expired or was revoked; pair again'
                raise PairingRequired(actual,self.identity.fingerprint,self.host_name,reason)
            expires = response.get('expires_at')
            if type(expires) is not int or expires<=0:raise SecurityError('Missing phone trust expiry')
            if mode == 'pair':
                self.state.setdefault('phones',{})[actual]={'expires_at':expires}
                self.store.update_pin(actual,{'expires_at':expires})
            channel.expires_at = expires
            return channel
        except BaseException:
            try: channel.close()
            except Exception: pass
            raise

    def _check_identity(self, response, phone):
        if response.get('phone_fingerprint') != phone or response.get('host_fingerprint') != self.identity.fingerprint:
            raise SecurityError('TLS identity binding mismatch')


class SecureTransport:
    def __init__(self, raw, context):
        self.raw = raw; self.incoming = ssl.MemoryBIO(); self.outgoing = ssl.MemoryBIO()
        self.tls = context.wrap_bio(self.incoming,self.outgoing,server_side=False)
        self.buffer = bytearray(); self.closed=False
        try:
            self._invoke(self.tls.do_handshake)
        except BaseException:
            self._drain()
            raise
        try:
            certificate = self.tls.getpeercert(binary_form=True)
            parsed = x509.load_der_x509_certificate(certificate)
            public = parsed.public_key()
            if not isinstance(public,ec.EllipticCurvePublicKey) or public.key_size<256:
                raise SecurityError('Phone did not present a strong EC identity')
            now = datetime.now(timezone.utc)
            if not parsed.not_valid_before_utc <= now <= parsed.not_valid_after_utc:
                raise SecurityError('Phone identity certificate is not currently valid')
            public.verify(parsed.signature,parsed.tbs_certificate_bytes,ec.ECDSA(parsed.signature_hash_algorithm))
            self.peer_fingerprint = hashlib.sha256(certificate).hexdigest()
        except BaseException:
            try:self.close()
            except Exception:pass
            raise

    def _drain(self):
        while self.outgoing.pending:
            payload=self.outgoing.read(MAX_FRAGMENT)
            self.raw.write(struct.pack('>BI',TLS_FRAME,len(payload))+payload)

    def _exact_raw(self,count):
        while len(self.buffer)<count:
            data=self.raw.read()
            if not data:raise SecurityError('USB closed during secure session')
            self.buffer.extend(data)
            if len(self.buffer)>131072:raise SecurityError('Secure input bound exceeded')
        data=bytes(self.buffer[:count]);del self.buffer[:count];return data

    def _feed(self):
        kind,length=struct.unpack('>BI',self._exact_raw(5))
        if kind!=TLS_FRAME or not 0<length<=MAX_FRAGMENT:raise SecurityError('Expected encrypted USB record; refusing plaintext')
        self.incoming.write(self._exact_raw(length))

    def _invoke(self, operation, *args):
        while True:
            try:
                value=operation(*args);self._drain();return value
            except ssl.SSLWantReadError:
                self._drain();self._feed()
            except ssl.SSLWantWriteError:self._drain()

    def write(self,data):
        view=memoryview(data)
        while view:
            count=self._invoke(self.tls.write,view);view=view[count:]

    def read(self):
        try:return self._invoke(self.tls.read,16384)
        except ssl.SSLZeroReturnError:return b''

    def write_control(self,value):
        data=json.dumps(value,separators=(',',':')).encode()
        if len(data)>8192:raise SecurityError('Security request too large')
        self.write(struct.pack('>I',len(data))+data)

    def _exact_plain(self,length):
        data=bytearray()
        while len(data)<length:
            try:chunk=self._invoke(self.tls.read,length-len(data))
            except ssl.SSLZeroReturnError:chunk=b''
            if not chunk:raise SecurityError('Secure session closed before authorization')
            data.extend(chunk)
        return bytes(data)

    def read_control(self):
        length=struct.unpack('>I',self._exact_plain(4))[0]
        if not 2<=length<=8192:raise SecurityError('Invalid security response size')
        value=json.loads(self._exact_plain(length))
        if not isinstance(value,dict):raise SecurityError('Invalid security response')
        return value

    def close(self):
        if self.closed:return
        self.closed=True
        try:self._invoke(self.tls.unwrap)
        finally:self._drain()
