package dev.pocketpull;

import android.app.Instrumentation;
import android.app.Activity;
import android.os.Bundle;
import android.os.Environment;
import java.io.*;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.UUID;

/** Framework-only contract checks plus explicitly opt-in physical acceptance fixtures. */
public final class StorageChecks extends Instrumentation {
    private boolean usbOnly, permissionsOnly, permissionDenied, usbBenchmark;
    private String base;
    private File directory;
    private Storage storage;
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); permissionDenied = arguments != null && "true".equals(arguments.getString("permissionDenied")); permissionsOnly = arguments != null && "true".equals(arguments.getString("permissions")); usbBenchmark = arguments != null && "true".equals(arguments.getString("benchmark")); usbOnly = arguments != null && "true".equals(arguments.getString("usb")); start(); }
    private void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private void check(String name, int number, Action action) throws Exception {
        Bundle state = new Bundle();
        state.putString("class", "dev.pocketpull.StorageChecks"); state.putString("test", name);
        state.putInt("numtests", 7); state.putInt("current", number);
        sendStatus(1, state);
        try { action.run(); sendStatus(0, state); }
        catch (Throwable error) { state.putString("stack", android.util.Log.getStackTraceString(error)); sendStatus(-2, state); throw new Exception(error); }
    }
    interface Action { void run() throws Exception; }
    private void rejects(Action action) throws Exception {
        try { action.run(); } catch (IOException expected) { return; }
        throw new AssertionError("Unsafe operation was accepted");
    }
    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            if (permissionDenied) {
                require(!Environment.isExternalStorageManager(), "Permission still granted");
                rejects(() -> Storage.shared(getTargetContext().getContentResolver()));
                MainActivity activity = (MainActivity) startActivitySync(new android.content.Intent(getTargetContext(), MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
                java.lang.reflect.Field field = MainActivity.class.getDeclaredField("selected"); field.setAccessible(true);
                require(field.get(activity) == null, "Selection survived revocation lifecycle");
                runOnMainSync(activity::finish);
                result.putString("stream", "OK (all-files revoked, storage refused, selection cleared)\n"); finish(Activity.RESULT_OK, result); return;
            }
            if (permissionsOnly) {
                PermissionChecks.run(this); result.putString("stream", "OK (physical permission checks cleaned)\n"); finish(Activity.RESULT_OK, result); return;
            }
            if (usbOnly) {
                USBHarness.run(this, usbBenchmark); result.putString("stream", "OK (physical USB fixture cleaned)\n"); finish(Activity.RESULT_OK, result); return;
            }
            storage = Storage.shared(getTargetContext().getContentResolver());
            base = "Download/PocketPull-check-" + UUID.randomUUID();
            directory = new File(Environment.getExternalStorageDirectory(), base);
            storage.mkdir(base);
            check("uploadDownloadAndList", 1, () -> {
                storage.mkdir(base + "/nested");
                byte[] bytes = new byte[40000];
                for (int i = 0; i < bytes.length; i++) bytes[i] = (byte)(i % 251);
                try (Storage.Upload upload = storage.upload(base + "/nested/data.bin")) {
                    upload.out.write(bytes); upload.out.close(); upload.committed = true;
                }
                require(Arrays.equals(bytes, Files.readAllBytes(new File(directory, "nested/data.bin").toPath())), "Stored content differs");
                require(storage.children(storage.resolve(base + "/nested")).size() == 1, "Directory listing differs");
                try (InputStream input = storage.input(storage.resolve(base + "/nested/data.bin"))) {
                    ByteArrayOutputStream copy = new ByteArrayOutputStream(); byte[] buffer = new byte[1024]; int n;
                    while ((n = input.read(buffer)) >= 0) copy.write(buffer, 0, n);
                    require(Arrays.equals(bytes, copy.toByteArray()), "Read content differs");
                }
            });
            check("failedUploadCleanupAndNoOverwrite", 2, () -> {
                try (Storage.Upload upload = storage.upload(base + "/partial")) { upload.out.write(1); }
                require(!new File(directory, "partial").exists(), "Partial upload remains");
                rejects(() -> storage.upload(base + "/nested/data.bin"));
                require(new File(directory, "nested/data.bin").length() == 40000, "Existing file changed");
            });
            check("pathContainment", 3, () -> {
                for (String path : new String[]{"../secret", "/sdcard", "a/../../x", "a//b", "a\\b", "a/"})
                    rejects(() -> storage.resolve(path));
                rejects(() -> storage.upload(""));
                rejects(() -> storage.mkdir("../elsewhere"));
            });
            check("downloadCancellationStopsAtChunkBoundary", 4, () -> {
                MainActivity activity = (MainActivity) startActivitySync(new android.content.Intent(getTargetContext(), MainActivity.class)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
                try {
                    java.lang.reflect.Field selected = MainActivity.class.getDeclaredField("selected");
                    selected.setAccessible(true); selected.set(activity, storage);
                    ByteArrayOutputStream output = new ByteArrayOutputStream();
                    Wire wire = new Wire(new ByteArrayInputStream(new byte[]{(byte) Wire.CANCEL, 0, 0, 0, 0}), output);
                    java.lang.reflect.Method transfer = MainActivity.class.getDeclaredMethod("transfer", Wire.class, Storage.class, Storage.Node.class, boolean.class);
                    transfer.setAccessible(true);
                    boolean cancelled = false;
                    try { transfer.invoke(activity, wire, storage, storage.resolve(base + "/nested/data.bin"), true); }
                    catch (java.lang.reflect.InvocationTargetException expected) { cancelled = "OperationCancelled".equals(expected.getCause().getClass().getSimpleName()); }
                    require(cancelled, "Cancellation was not handled at download acknowledgment");
                    ByteArrayInputStream bytes = new ByteArrayInputStream(output.toByteArray());
                    Wire responses = new Wire(bytes, new ByteArrayOutputStream());
                    require(responses.receive().kind == Wire.META, "Missing file metadata");
                    Wire.Frame first = responses.receive();
                    require(first.kind == Wire.DATA && first.payload.length == Wire.MAX && bytes.available() == 0, "Download continued after cancellation");
                    require(new File(directory, "nested/data.bin").length() == 40000, "Source file changed after cancellation");
                } finally { runOnMainSync(activity::finish); }
            });
            check("batchedDownloadCancelsAfterFinalPartialWindow", 5, () -> {
                MainActivity activity = (MainActivity) startActivitySync(new android.content.Intent(getTargetContext(), MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
                try {
                    java.lang.reflect.Field field = MainActivity.class.getDeclaredField("selected"); field.setAccessible(true); field.set(activity, storage);
                    ByteArrayOutputStream output = new ByteArrayOutputStream();
                    Wire wire = new Wire(new ByteArrayInputStream(new byte[]{(byte)Wire.CANCEL, 0, 0, 0, 0}), output);
                    java.lang.reflect.Method transfer = MainActivity.class.getDeclaredMethod("transfer", Wire.class, Storage.class, Storage.Node.class, boolean.class, org.json.JSONObject.class); transfer.setAccessible(true);
                    boolean cancelled=false;
                    try { transfer.invoke(activity, wire, storage, storage.resolve(base+"/nested/data.bin"), true, new org.json.JSONObject().put("batch_frames",16)); }
                    catch(java.lang.reflect.InvocationTargetException expected) { cancelled="OperationCancelled".equals(expected.getCause().getClass().getSimpleName()); }
                    require(cancelled,"Batched download did not cancel at partial window boundary");
                    ByteArrayInputStream raw = new ByteArrayInputStream(output.toByteArray()); Wire responses = new Wire(raw,new ByteArrayOutputStream());
                    require(new org.json.JSONObject(responses.receive().text()).getInt("batch_frames")==16,"Batch mode was not accepted");
                    long bytes=0;int frames=0;Wire.Frame frame;
                    while((frame=responses.receive()).kind==Wire.DATA) {bytes+=frame.payload.length;frames++;}
                    require(frames==3 && bytes==40000 && frame.kind==Wire.META && new org.json.JSONObject(frame.text()).getLong("bytes")==40000 && raw.available()==0,"Batched download continued after cancellation or omitted boundary");
                } finally {runOnMainSync(activity::finish);}
            });
            check("uploadBatchBoundaryAndMemoryBound", 6, () -> {
                MainActivity activity = (MainActivity) startActivitySync(new android.content.Intent(getTargetContext(), MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
                try {
                    java.lang.reflect.Method receive = MainActivity.class.getDeclaredMethod("receiveUploadBatch",Wire.class,int.class,long.class);receive.setAccessible(true);
                    ByteArrayOutputStream input=new ByteArrayOutputStream();Wire sender=new Wire(new ByteArrayInputStream(new byte[0]),input);
                    sender.send(Wire.DATA,new byte[]{1,2});sender.send(Wire.DATA,new byte[]{3});sender.send(Wire.META,"{\"batch\":true,\"bytes\":10}");
                    Wire.Frame batch=(Wire.Frame)receive.invoke(activity,new Wire(new ByteArrayInputStream(input.toByteArray()),new ByteArrayOutputStream()),16,7L);
                    require(batch.kind==Wire.DATA && Arrays.equals(batch.payload,new byte[]{1,2,3}),"Upload batch concatenation or offset differs");
                    input.reset();for(int i=0;i<17;i++)sender.send(Wire.DATA,new byte[]{1});
                    boolean rejected=false;
                    try{receive.invoke(activity,new Wire(new ByteArrayInputStream(input.toByteArray()),new ByteArrayOutputStream()),16,0L);}
                    catch(java.lang.reflect.InvocationTargetException expected){rejected=expected.getCause() instanceof IOException;}
                    require(rejected,"Oversized upload batch accepted");
                } finally {runOnMainSync(activity::finish);}
            });
            check("keystoreTlsPrehashedSigningAndIdentityPersistence", 7, () -> {
                String alias="PocketDimension-security-check-"+UUID.randomUUID();
                java.security.KeyStore keys=java.security.KeyStore.getInstance("AndroidKeyStore");keys.load(null);
                try {
                    dev.pocketdimension.usb.AndroidIdentity identity=new dev.pocketdimension.usb.AndroidIdentity(alias);
                    require(identity.fingerprint().equals(new dev.pocketdimension.usb.AndroidIdentity(alias).fingerprint()),"Identity changed on reopening");
                    java.security.PrivateKey key=(java.security.PrivateKey)keys.getKey(alias,null);
                    require(key.getEncoded()==null,"Keystore private key was exportable");
                    byte[] digest=java.security.MessageDigest.getInstance("SHA-256").digest("TLS fixture".getBytes(java.nio.charset.StandardCharsets.UTF_8));
                    java.security.Signature signer=java.security.Signature.getInstance("NONEwithECDSA");signer.initSign(key);signer.update(digest);byte[] signature=signer.sign();
                    java.security.Signature verifier=java.security.Signature.getInstance("NONEwithECDSA");verifier.initVerify(keys.getCertificate(alias));verifier.update(digest);
                    require(verifier.verify(signature),"Platform TLS prehashed signature failed");
                } finally { keys.deleteEntry(alias); }
            });
            result.putString("stream", "OK (7 Android storage, cancellation, batching, and Keystore checks)\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            result.putString("stream", android.util.Log.getStackTraceString(error));
            finish(Activity.RESULT_CANCELED, result);
        } finally { if (directory != null) remove(directory); }
    }
    private void remove(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) remove(child);
        file.delete();
    }
}
