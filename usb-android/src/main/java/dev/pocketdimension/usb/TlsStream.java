package dev.pocketdimension.usb;

import javax.net.ssl.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.util.Arrays;

/** TLS over bounded USB records. Uses JSSE for all cryptography; one I/O owner thread. */
public final class TlsStream implements AutoCloseable {
    public interface Records {
        byte[] receive() throws IOException;
        void send(byte[] record) throws IOException;
        void abort();
    }
    public static final int MAX_RECORD_FRAGMENT=16379;
    private final SSLEngine engine;
    private final Records records;
    private final ByteBuffer network=ByteBuffer.allocateDirect(65536);
    private final ByteBuffer application=ByteBuffer.allocateDirect(32768);
    private final ByteBuffer outgoing=ByteBuffer.allocateDirect(32768);
    private static final ByteBuffer EMPTY=ByteBuffer.allocate(0);
    private boolean inboundClosed,closed;
    private boolean initialHandshake=true;
    private int handshakeBytes;
    public TlsStream(SSLEngine engine,Records records) throws IOException {
        this.engine=engine;this.records=records;network.flip();application.flip();
        engine.beginHandshake();handshake();initialHandshake=false;
    }
    public SSLSession session(){return engine.getSession();}
    private void tasks(){Runnable task;while((task=engine.getDelegatedTask())!=null)task.run();}
    private void sendOutput() throws IOException {
        outgoing.flip();
        while(outgoing.hasRemaining()){
            byte[] bytes=new byte[Math.min(outgoing.remaining(),MAX_RECORD_FRAGMENT)];outgoing.get(bytes);records.send(bytes);
        }
    }
    private void refill() throws IOException {
        network.compact();byte[] next=records.receive();
        if(next.length==0||next.length>MAX_RECORD_FRAGMENT||next.length>network.remaining())throw new SSLException("Invalid TLS record fragment");
        if(initialHandshake && (handshakeBytes+=next.length)>262144)throw new SSLException("TLS handshake byte limit exceeded");
        network.put(next);network.flip();
    }
    private SSLEngineResult wrap(ByteBuffer bytes) throws IOException {
        outgoing.clear();SSLEngineResult result=engine.wrap(bytes,outgoing);
        if(result.getStatus()==SSLEngineResult.Status.BUFFER_OVERFLOW)throw new SSLException("TLS output bound exceeded");
        sendOutput();return result;
    }
    private SSLEngineResult unwrap() throws IOException {
        application.compact();
        SSLEngineResult result;
        try{result=engine.unwrap(network,application);}
        catch(SSLException error){throw new SSLException("TLS unwrap failed; buffered="+network.remaining()+", state="+engine.getHandshakeStatus(),error);}
        application.flip();
        if(result.getStatus()==SSLEngineResult.Status.BUFFER_OVERFLOW)throw new SSLException("TLS application bound exceeded");
        if(result.getStatus()==SSLEngineResult.Status.BUFFER_UNDERFLOW)refill();
        if(result.getStatus()==SSLEngineResult.Status.CLOSED)inboundClosed=true;
        return result;
    }
    private void handshake() throws IOException {
        int noProgress=0;
        while(true){
            SSLEngineResult.HandshakeStatus status=engine.getHandshakeStatus();
            if(status.name().equals("NEED_UNWRAP_AGAIN")){unwrap();continue;}
            switch(status){
                case NOT_HANDSHAKING:case FINISHED:return;
                case NEED_TASK:tasks();break;
                case NEED_WRAP:wrap(EMPTY.duplicate());break;
                case NEED_UNWRAP:
                    if(inboundClosed)throw new EOFException("TLS closed during handshake");
                    // NEED_UNWRAP waits for peer bytes; NEED_UNWRAP_AGAIN
                    // above handles providers with buffered handshake work.
                    if(!network.hasRemaining())refill();
                    unwrap();break;
                default:throw new SSLException("Unsupported TLS handshake state");
            }
            // Successful calls either consume buffered data or block on bounded USB input.
            if(++noProgress>8192)throw new SSLException("TLS handshake step limit exceeded");
        }
    }
    public final InputStream input=new InputStream(){
        @Override public int read()throws IOException{byte[] one=new byte[1];return read(one,0,1)<0?-1:one[0]&255;}
        @Override public int read(byte[] bytes,int offset,int length)throws IOException{
            if(offset<0||length<0||offset>bytes.length-length)throw new IndexOutOfBoundsException();if(length==0)return 0;
            while(!application.hasRemaining()){
                if(inboundClosed)return -1;
                handshake();if(application.hasRemaining())break;unwrap();
            }
            int count=Math.min(length,application.remaining());application.get(bytes,offset,count);return count;
        }
    };
    public final OutputStream output=new OutputStream(){
        @Override public void write(int value)throws IOException{write(new byte[]{(byte)value});}
        @Override public void write(byte[] bytes,int offset,int length)throws IOException{
            if(offset<0||length<0||offset>bytes.length-length)throw new IndexOutOfBoundsException();
            ByteBuffer source=ByteBuffer.wrap(bytes,offset,length);
            while(source.hasRemaining()){
                if(closed)throw new EOFException("TLS session closed");
                handshake();SSLEngineResult result=wrap(source);
                if(result.getStatus()==SSLEngineResult.Status.CLOSED)throw new EOFException("TLS session closed");
                if(result.bytesConsumed()==0&&result.bytesProduced()==0)throw new SSLException("TLS output made no progress");
            }
        }
    };
    // Before returning a still-open USB transport to public discovery, consume
    // the peer's close_notify so it cannot become the next upgrade request.
    void awaitPeerClose()throws IOException{
        while(!inboundClosed){
            if(application.hasRemaining())throw new SSLException("Unexpected data after security control");
            unwrap();
        }
    }
    public void abort(){closed=true;records.abort();}
    @Override public void close()throws IOException{
        if(closed)return;closed=true;engine.closeOutbound();wrap(EMPTY.duplicate());
    }
}
