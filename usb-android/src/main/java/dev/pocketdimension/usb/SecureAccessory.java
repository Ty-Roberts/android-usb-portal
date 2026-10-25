package dev.pocketdimension.usb;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import org.json.*;
import javax.net.ssl.*;
import java.io.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.util.*;
import java.util.concurrent.*;

/** Reusable USB security boundary. Owns trust; consuming apps own UI and data scope. */
public final class SecureAccessory implements AutoCloseable {
    public interface Approval {
        /** Runs on the I/O worker. UI must compare BOTH full fingerprints; default deny. */
        boolean approve(PairingRequest request) throws Exception;
    }
    public static final class PairingRequest {
        public final String computerName, computerFingerprint, phoneFingerprint;
        public final long validUntil;
        private PairingRequest(String name,String host,String phone,long until){computerName=name;computerFingerprint=host;phoneFingerprint=phone;validUntil=until;}
    }
    private final TlsIdentity identity;
    private final TrustPolicy trust;
    private final Approval approval;
    private final java.util.function.BooleanSupplier userPresent;
    private final ScheduledExecutorService timer=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"SecureUSB-deadlines");t.setDaemon(true);return t;});
    private final Set<Session> sessions=ConcurrentHashMap.newKeySet();
    private final Set<TlsStream.Records> pending=ConcurrentHashMap.newKeySet();
    private final Object lifecycle=new Object();
    private long generation;
    private boolean closed;
    public SecureAccessory(Context context,String namespace,Approval approval)throws Exception {
        if(namespace==null||!namespace.matches("[A-Za-z0-9._-]{1,100}"))throw new IllegalArgumentException("Invalid security namespace");
        android.app.KeyguardManager keyguard=(android.app.KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE);
        userPresent=()->!keyguard.isKeyguardLocked();
        identity=new AndroidIdentity(namespace+".identity-v1");this.approval=Objects.requireNonNull(approval);
        SharedPreferences prefs=context.getApplicationContext().getSharedPreferences(namespace+".trust",Context.MODE_PRIVATE);
        String local=identity.fingerprint();
        if(!local.equals(prefs.getString("identity",""))) {
            if(!prefs.edit().clear().putString("identity",local).commit())throw new IOException("Cannot initialize trust storage");
        }
        TrustPolicy.Store store=new TrustPolicy.Store(){
            public Map<String,TrustPolicy.Grant> load(){
                Map<String,TrustPolicy.Grant> values=new HashMap<>();
                try{
                    JSONArray array=new JSONArray(prefs.getString("hosts","[]"));
                    if(array.length()>100)throw new JSONException("Too many hosts");
                    for(int i=0;i<array.length();i++){
                        JSONObject item=array.getJSONObject(i);String fp=item.getString("fingerprint");
                        if(!fp.matches("[0-9a-f]{64}"))throw new JSONException("Invalid fingerprint");
                        values.put(fp,new TrustPolicy.Grant(fp,item.getString("name"),item.getLong("approved"),item.getLong("expires")));
                    }
                }catch(Exception invalid){values.clear();}
                return values;
            }
            public long lastSeenWall(){return prefs.getLong("last_seen",0);}
            public void save(Map<String,TrustPolicy.Grant> values,long lastSeen){
                JSONArray array=new JSONArray();
                try{for(TrustPolicy.Grant grant:values.values())array.put(new JSONObject().put("fingerprint",grant.fingerprint).put("name",grant.name).put("approved",grant.approvedAt).put("expires",grant.expiresAt));}
                catch(JSONException error){throw new IllegalStateException(error);}
                if(!prefs.edit().putString("hosts",array.toString()).putLong("last_seen",lastSeen).commit())throw new IllegalStateException("Trust change could not be saved");
            }
        };
        trust=new TrustPolicy(new TrustPolicy.Clock(){public long wallMillis(){return System.currentTimeMillis();}public long elapsedMillis(){return SystemClock.elapsedRealtime();}},store);
    }
    // Package-private dependency injection for JVM protocol tests; public Android
    // construction always uses Keystore, private preferences, and the real lock state.
    SecureAccessory(TlsIdentity identity, TrustPolicy trust, Approval approval, java.util.function.BooleanSupplier userPresent) {
        this.identity=identity;this.trust=trust;this.approval=approval;this.userPresent=userPresent;
    }
    public String fingerprint()throws GeneralSecurityException{return identity.fingerprint();}
    public List<TrustPolicy.Grant> trustedComputers(){return trust.list();}
    public void revoke(String fingerprint){
        synchronized(lifecycle){
            generation++;for(TlsStream.Records records:pending)records.abort();
            try{trust.revoke(fingerprint);}finally{for(Session s:sessions)if(s.computerFingerprint.equals(fingerprint))s.invalidate();}
        }
    }
    public void revokeAll(){synchronized(lifecycle){try{trust.revokeAll();}finally{invalidateSessions();}}}
    public void invalidateSessions(){synchronized(lifecycle){generation++;for(TlsStream.Records records:pending)records.abort();for(Session session:sessions)session.invalidate();}}
    private void requireCurrent(long started){if(closed||generation!=started)throw new SecurityException("USB authorization cancelled");}
    private static JSONObject readControl(InputStream input)throws Exception{
        DataInputStream source=new DataInputStream(input);int length=source.readInt();
        if(length<2||length>8192)throw new IOException("Invalid security control length");
        byte[] bytes=new byte[length];source.readFully(bytes);return new JSONObject(new String(bytes,java.nio.charset.StandardCharsets.UTF_8));
    }
    private static void writeControl(OutputStream output,JSONObject object)throws Exception{
        byte[] bytes=object.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
        if(bytes.length>8192)throw new IOException("Security control too large");
        DataOutputStream target=new DataOutputStream(output);target.writeInt(bytes.length);target.write(bytes);target.flush();
    }
    /** Returns null for inspection/denial; never returns unauthenticated application streams. */
    public Session accept(TlsStream.Records records)throws Exception{
        final long started;
        final ScheduledFuture<?> timeout;
        synchronized(lifecycle){
            if(closed)throw new IOException("Secure USB owner closed");
            started=generation;pending.add(records);
            timeout=timer.schedule(records::abort,90,TimeUnit.SECONDS);
        }
        TlsStream tls=null;boolean returned=false;
        try{
            tls=new TlsStream(identity.serverEngine(),records);
            X509Certificate peer=(X509Certificate)tls.session().getPeerCertificates()[0];
            String host=AndroidIdentity.fingerprint(peer),phone=identity.fingerprint();
            JSONObject request=readControl(tls.input);String mode=request.optString("mode","");
            TrustPolicy.Authorization auth=trust.authorize(host);
            JSONObject response=new JSONObject().put("phone_fingerprint",phone).put("host_fingerprint",host);
            if(mode.equals("inspect")) {writeControl(tls.output,response.put("status",auth==null?"pairing_required":"trusted"));return null;}
            if(mode.equals("revoke")){pending.remove(records);revoke(host);writeControl(tls.output,response.put("status","revoked"));return null;}
            if(!mode.equals("connect")&&!mode.equals("pair"))throw new IOException("Unknown security request");
            if(!phone.equals(request.optString("expected_phone_fingerprint","")))throw new SecurityException("Phone fingerprint confirmation required");
            if(!userPresent.getAsBoolean())throw new SecurityException("Unlock the phone to authorize USB access");
            if(mode.equals("pair")){
                String name=request.optString("host_name","");
                if(name.trim().isEmpty()||name.length()>80||name.chars().anyMatch(c->c<32||c>126))throw new IOException("Invalid computer name");
                if(trust.list().size()>=100&&auth==null)throw new IOException("Trusted computer limit reached");
                long until=System.currentTimeMillis()+TrustPolicy.TRUST_MILLIS;
                if(!approval.approve(new PairingRequest(name,host,phone,until))){writeControl(tls.output,response.put("status","denied"));return null;}
                byte[] nonce=new byte[32];new SecureRandom().nextBytes(nonce);String challenge=Base64.getEncoder().encodeToString(nonce);
                writeControl(tls.output,response.put("status","approved").put("confirmation",challenge));
                JSONObject confirm=readControl(tls.input);
                if(!challenge.equals(confirm.optString("confirmation","")))throw new SecurityException("Pairing confirmation mismatch");
                if(!userPresent.getAsBoolean())throw new SecurityException("Phone locked during pairing");
                synchronized(lifecycle){requireCurrent(started);trust.approve(host,name);auth=trust.authorize(host);}
            }
            if(auth==null){writeControl(tls.output,response.put("status","pairing_required"));return null;}
            Session session;
            synchronized(lifecycle){
                requireCurrent(started);auth.requireValid();
                session=new Session(tls,auth,host);sessions.add(session);pending.remove(records);
                session.expiry=timer.schedule(session::invalidate,auth.remainingMillis(),TimeUnit.MILLISECONDS);
            }
            try{writeControl(tls.output,response.put("status","authorized").put("expires_at",auth.grant.expiresAt));}
            catch(Exception error){session.invalidate();throw error;}
            returned=true;return session;
        }finally{
            try{
                if(!returned&&tls!=null)try{tls.awaitPeerClose();tls.close();}catch(IOException ignored){records.abort();}
            }finally{timeout.cancel(false);pending.remove(records);}
        }
    }
    public final class Session implements AutoCloseable{
        private final TlsStream tls;
        private final TrustPolicy.Authorization authorization;
        public final String computerFingerprint;
        public final long expiresAt;
        private volatile boolean closed;
        private ScheduledFuture<?> expiry;
        private Session(TlsStream tls,TrustPolicy.Authorization authorization,String fp){this.tls=tls;this.authorization=authorization;computerFingerprint=fp;expiresAt=authorization.grant.expiresAt;}
        public void requireAuthorized()throws IOException{
            if(!userPresent.getAsBoolean()){invalidate();throw new IOException("Phone locked; reconnect after unlocking");}
            if(closed)throw new IOException("Secure USB session closed");
            try{authorization.requireValid();}catch(SecurityException error){invalidate();throw new IOException(error.getMessage(),error);}
        }
        public void invalidate(){closed=true;authorization.invalidate();tls.abort();sessions.remove(this);if(expiry!=null)expiry.cancel(false);}
        public final InputStream input=new InputStream(){
            public int read()throws IOException{byte[] b=new byte[1];return read(b,0,1)<0?-1:b[0]&255;}
            public int read(byte[] b,int off,int length)throws IOException{requireAuthorized();int count=tls.input.read(b,off,length);requireAuthorized();return count;}
        };
        public final OutputStream output=new OutputStream(){
            public void write(int value)throws IOException{write(new byte[]{(byte)value});}
            public void write(byte[] b,int off,int length)throws IOException{requireAuthorized();tls.output.write(b,off,length);requireAuthorized();}
        };
        public void close()throws IOException{if(closed)return;closed=true;authorization.invalidate();sessions.remove(this);if(expiry!=null)expiry.cancel(false);tls.close();}
    }
    @Override public void close(){synchronized(lifecycle){closed=true;invalidateSessions();timer.shutdownNow();}}
}
