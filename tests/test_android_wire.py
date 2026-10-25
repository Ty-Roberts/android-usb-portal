"""Compile the actual Android-independent Java wire codec and consume it in Python."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from pocketpull.protocol import Connection
from test_protocol import Transport

HARNESS = r'''
package dev.pocketpull;
import java.io.*;
import java.security.MessageDigest;
import java.util.Arrays;
public class WireHarness {
    public static void main(String[] args) throws Exception {
        if (args.length > 0 && args[0].equals("paths")) {
            for (String bad : new String[]{"../x", "/absolute", "a//b", "a/../b", "a\\b", "a/"}) {
                try { SafePaths.parts(bad); throw new AssertionError("Accepted " + bad); }
                catch (IOException expected) { }
            }
            if (SafePaths.parts("photos/猫.jpg").length != 2) throw new AssertionError();
            return;
        }
        if (args.length > 0 && args[0].equals("frames")) {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            Wire writer = new Wire(new ByteArrayInputStream(new byte[0]), bytes);
            writer.send(Wire.PUSH, "{\"path\":\"test\",\"size\":16379}");
            writer.send(Wire.DATA, new byte[Wire.MAX]);
            writer.send(Wire.END, "{}");
            Wire reader = new Wire(new ByteArrayInputStream(bytes.toByteArray()), new ByteArrayOutputStream());
            if (reader.receive().kind != Wire.PUSH || reader.receive().payload.length != Wire.MAX || reader.receive().kind != Wire.END) throw new AssertionError();
            return;
        }
        byte[] commands = {1, 0, 0, 0, 0, 3, 0, 0, 0, 0};
        if (args.length > 0) commands[4] = 1;
        ByteArrayInputStream source = new ByteArrayInputStream(commands);
        InputStream fragmented = new FilterInputStream(source) {
            public int read(byte[] b, int off, int len) throws IOException {
                return super.read(b, off, Math.min(len, 2));
            }
        };
        Wire wire = new Wire(fragmented, System.out);
        if (args.length > 0) {
            try { wire.command(); throw new AssertionError("Accepted payload"); }
            catch (IOException expected) { return; }
        }
        if (wire.command() != Wire.HELLO || wire.command() != Wire.GET) throw new AssertionError();
        wire.send(Wire.HELLO, "{\"version\":1}");
        wire.send(Wire.META, "{\"name\":\"java.bin\"}");
        byte[] data = new byte[Wire.MAX + 123];
        for (int i = 0; i < data.length; i++) data[i] = (byte)(i % 251);
        wire.send(Wire.DATA, Arrays.copyOfRange(data, 0, Wire.MAX));
        wire.send(Wire.DATA, Arrays.copyOfRange(data, Wire.MAX, data.length));
        StringBuilder hash = new StringBuilder();
        for (byte value : MessageDigest.getInstance("SHA-256").digest(data)) hash.append(String.format("%02x", value & 255));
        wire.send(Wire.END, "{\"bytes\":" + data.length + ",\"sha256\":\"" + hash + "\"}");
    }
}
'''


def LegacyConnection(*args, **kwargs):
    return Connection(*args, allow_legacy=True, **kwargs)


class AndroidWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        java_home = os.environ.get("JAVA_HOME", "/Applications/Android Studio.app/Contents/jbr/Contents/Home")
        cls.java = str(Path(java_home) / "bin/java")
        javac = str(Path(java_home) / "bin/javac")
        if not Path(javac).exists():
            javac, cls.java = shutil.which("javac"), shutil.which("java")
        if not javac:
            raise unittest.SkipTest("JDK required for Java/Python interoperability tests")
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        harness = Path(cls.directory.name) / "WireHarness.java"
        harness.write_text(HARNESS)
        subprocess.run([javac, "-d", cls.directory.name, "app/src/main/java/dev/pocketpull/Wire.java", "app/src/main/java/dev/pocketpull/SafePaths.java", str(harness)], check=True)

    def run_harness(self, *args):
        return subprocess.check_output([self.java, "-cp", self.directory.name, "dev.pocketpull.WireHarness", *args])

    def test_java_frames_download_verified_by_python(self):
        connection = LegacyConnection(Transport(self.run_harness(), 113))
        connection.hello()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "java.bin"
            result = connection.pull(destination)
            expected = bytes(i % 251 for i in range(16379 + 123))
            self.assertEqual(destination.read_bytes(), expected)
            self.assertEqual(result["bytes"], len(expected))

    def test_android_path_containment(self):
        self.assertEqual(self.run_harness("paths"), b"")

    def test_android_receives_upload_frames(self):
        self.assertEqual(self.run_harness("frames"), b"")

    def test_android_rejects_command_payload(self):
        self.assertEqual(self.run_harness("invalid"), b"")
