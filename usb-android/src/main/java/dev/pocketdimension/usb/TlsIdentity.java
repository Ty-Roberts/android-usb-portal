package dev.pocketdimension.usb;

import javax.net.ssl.SSLEngine;
import java.security.GeneralSecurityException;

interface TlsIdentity {
    String fingerprint() throws GeneralSecurityException;
    SSLEngine serverEngine() throws GeneralSecurityException;
}
