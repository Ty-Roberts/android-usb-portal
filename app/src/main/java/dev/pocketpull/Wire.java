package dev.pocketpull;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

/** Full-size accessory reads and bounded application frames in either direction. */
final class Wire {
    static final int HELLO = 1, LIST = 2, GET = 3, META = 4, DATA = 5, END = 6, ERROR = 7;
    static final int BROWSE = 8, PUSH = 9, MKDIR = 10, STAT = 11, CANCEL = 12;
    static final int SECURE = 13;
    static final int MAX = 16379;
    private final DataInputStream input;
    private final OutputStream output;
    static final class Frame {
        final int kind;
        final byte[] payload;
        Frame(int kind, byte[] payload) { this.kind = kind; this.payload = payload; }
        String text() { return new String(payload, StandardCharsets.UTF_8); }
    }
    Wire(InputStream input, OutputStream output) {
        this.input = new DataInputStream(new BufferedInputStream(input, 16384));
        this.output = output;
    }
    Frame receive() throws IOException {
        int kind = input.readUnsignedByte(), length = input.readInt();
        if (length < 0 || length > MAX) throw new IOException("Invalid frame length");
        byte[] payload = new byte[length];
        input.readFully(payload);
        return new Frame(kind, payload);
    }
    int command() throws IOException {
        Frame frame = receive();
        if (frame.payload.length != 0) throw new IOException("Expected empty command");
        return frame.kind;
    }
    void send(int kind, String text) throws IOException { send(kind, text.getBytes(StandardCharsets.UTF_8)); }
    void send(int kind, byte[] data) throws IOException {
        if (data.length > MAX) throw new IOException("Frame too large");
        output.write(ByteBuffer.allocate(5 + data.length).put((byte)kind).putInt(data.length).put(data).array());
    }
}
