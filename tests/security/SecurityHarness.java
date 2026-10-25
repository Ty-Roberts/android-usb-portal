package dev.pocketdimension.usb;

import java.io.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.util.*;
import javax.net.ssl.*;

/** Runs the actual library TLS, authorization, and expiry code over framed stdio. */
public class SecurityHarness {
    static final class Clock implements TrustPolicy.Clock {
        long wall=System.currentTimeMillis(),elapsed=1000000;
        public long wallMillis(){return wall;}public long elapsedMillis(){return elapsed;}
        void advance(long amount){wall+=amount;elapsed+=amount;}
    }
    static final class Store implements TrustPolicy.Store {
        Map<String,TrustPolicy.Grant> grants=new HashMap<>();long last;
        public Map<String,TrustPolicy.Grant> load(){return new HashMap<>(grants);}
        public long lastSeenWall(){return last;}
        public void save(Map<String,TrustPolicy.Grant> values,long seen){grants=new HashMap<>(values);last=seen;}
    }
    static void require(boolean value){if(!value)throw new AssertionError();}
    static void policyChecks(){
        Clock clock=new Clock();Store store=new Store();TrustPolicy policy=new TrustPolicy(clock,store);String fp="a".repeat(64);
        require(policy.authorize(fp)==null);
        TrustPolicy.Grant first=policy.approve(fp,"Fixture");TrustPolicy.Authorization session=policy.authorize(fp);session.requireValid();
        clock.advance(TrustPolicy.TRUST_MILLIS-1);policy.authorize(fp).requireValid();require(policy.list().get(0).expiresAt==first.expiresAt);
        clock.advance(1);require(policy.authorize(fp)==null);
        try{session.requireValid();throw new AssertionError();}catch(SecurityException expected){}
        policy.approve(fp,"Fixture");session=policy.authorize(fp);policy.revoke(fp);require(policy.authorize(fp)==null);
        policy.approve(fp,"Fixture");try{session.requireValid();throw new AssertionError();}catch(SecurityException expected){}
        session=policy.authorize(fp);clock.elapsed+=TrustPolicy.TRUST_MILLIS;
        try{session.requireValid();throw new AssertionError();}catch(SecurityException expected){}
        clock.wall-=86400000;try{policy.list();throw new AssertionError();}catch(SecurityException expected){}
        require(policy.list().isEmpty());policy.approve(fp,"Fixture");TrustPolicy restored=new TrustPolicy(clock,store);require(restored.authorize(fp)!=null);
        System.out.println("PASS fixed expiry, no sliding renewal, revocation, old-session invalidation, monotonic deadline, clock rollback, and persistence");
    }
    public static void main(String[] args)throws Exception{
        if(args[0].equals("policy")){policyChecks();return;}
        String mode=args[0];KeyStore keys=KeyStore.getInstance("PKCS12");
        try(InputStream in=new FileInputStream(args[1])){keys.load(in,"fixture".toCharArray());}
        X509Certificate certificate=(X509Certificate)keys.getCertificate("fixture");
        String phone=AndroidIdentity.fingerprint(certificate);
        KeyManagerFactory km=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());km.init(keys,"fixture".toCharArray());
        X509TrustManager proof=new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String type)throws java.security.cert.CertificateException{if(chain==null||chain.length!=1)throw new java.security.cert.CertificateException();}
            public void checkServerTrusted(X509Certificate[] chain,String type)throws java.security.cert.CertificateException{throw new java.security.cert.CertificateException();}
        };
        SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(km.getKeyManagers(),new TrustManager[]{proof},new SecureRandom());
        TlsIdentity identity=new TlsIdentity(){
            public String fingerprint(){return phone;}
            public SSLEngine serverEngine(){SSLEngine engine=ssl.createSSLEngine();engine.setUseClientMode(false);engine.setNeedClientAuth(true);engine.setEnabledProtocols(new String[]{mode.equals("tls12")?"TLSv1.2":"TLSv1.3"});return engine;}
        };
        Clock clock=new Clock();TrustPolicy policy=new TrustPolicy(clock,new Store());
        if(mode.equals("trusted")||mode.equals("expire")||mode.equals("tls12")||mode.equals("locked"))policy.approve(args[2],"Fixture");
        DataInputStream rawIn=new DataInputStream(System.in);DataOutputStream rawOut=new DataOutputStream(System.out);
        TlsStream.Records records=new TlsStream.Records(){
            public byte[] receive()throws IOException{int kind=rawIn.readUnsignedByte(),count=rawIn.readInt();if(kind!=13||count<1||count>16379)throw new IOException("Bad secure record");byte[] b=new byte[count];rawIn.readFully(b);return b;}
            public void send(byte[] data)throws IOException{rawOut.writeByte(13);rawOut.writeInt(data.length);rawOut.write(data);rawOut.flush();}
            public void abort(){try{rawIn.close();rawOut.close();}catch(IOException ignored){}}
        };
        final SecureAccessory[] holder=new SecureAccessory[1];
        try(SecureAccessory endpoint=new SecureAccessory(identity,policy,request->{
            if(mode.equals("cancel_pair")){holder[0].invalidateSessions();return true;}
            return mode.equals("pair")||mode.equals("reuse");
        },()->!mode.equals("locked"))){
            holder[0]=endpoint;
            SecureAccessory.Session accepted=endpoint.accept(records);
            if(accepted==null&&mode.equals("reuse"))accepted=endpoint.accept(records);
            if(accepted==null)return;
            final SecureAccessory.Session session=accepted;
            try(session){
                DataInputStream input=new DataInputStream(session.input);DataOutputStream output=new DataOutputStream(session.output);
                while(true){
                    int size;try{size=input.readInt();}catch(EOFException end){break;}
                    if(size<0||size>1048576)throw new IOException("Payload limit");byte[] payload=new byte[size];input.readFully(payload);
                    if(mode.equals("expire")){clock.advance(TrustPolicy.TRUST_MILLIS);try{session.output.write(payload);throw new AssertionError("Expired data escaped");}catch(IOException expected){return;}}
                    output.writeInt(size);output.write(payload);output.flush();
                }
            }
        }
    }
}
