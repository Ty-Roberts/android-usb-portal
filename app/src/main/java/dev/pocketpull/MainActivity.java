package dev.pocketpull;

import android.app.*;
import android.content.*;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.hardware.usb.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import dev.pocketdimension.usb.SecureAccessory;
import dev.pocketdimension.usb.TlsStream;
import dev.pocketdimension.usb.TrustPolicy;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int PICK = 10, PICK_FOLDER = 11;
    private boolean awaitingAllFiles;
    private static final String PERMISSION = "dev.pocketpull.USB_PERMISSION";
    private UsbManager usb;
    private String deviceId;
    private TextView connectionText, fileText, transferText;
    private volatile Storage selected;
    private int selectionGeneration;
    private volatile boolean destroyed;
    private volatile ParcelFileDescriptor descriptor;
    private volatile SecureAccessory secureOwner;
    private final ThreadLocal<SecureAccessory.Session> threadSession = new ThreadLocal<>();
    private volatile CompletableFuture<Boolean> pendingApproval;
    private boolean resumed;
    private final ExecutorService metadataWorker = Executors.newSingleThreadExecutor();
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            // Re-query UsbManager; never trust an exported intent's accessory/permission extras.
            if (UsbManager.ACTION_USB_ACCESSORY_DETACHED.equals(intent.getAction())) {
                clearSelection(); closeConnection();
                connectionText.setText(getString(R.string.usb_disconnected));
            } else if (Intent.ACTION_SCREEN_OFF.equals(intent.getAction())) { clearSelection(); closeConnection(); }
            else connect(false);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        usb = (UsbManager) getSystemService(USB_SERVICE);
        android.content.SharedPreferences identity = getSharedPreferences("identity", MODE_PRIVATE);
        deviceId = identity.getString("id", null);
        if (deviceId == null) { deviceId = java.util.UUID.randomUUID().toString(); identity.edit().putString("id", deviceId).apply(); }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        buildUi();
        IntentFilter filter = new IntentFilter(PERMISSION);
        filter.addAction(UsbManager.ACTION_USB_ACCESSORY_DETACHED);
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        else registerLegacyReceiver(filter);
        connect(false);
    }

    // API 26–32 have no exported/not-exported receiver flags. Permission is rechecked via UsbManager.
    @android.annotation.SuppressLint("UnspecifiedRegisterReceiverFlag")
    private void registerLegacyReceiver(IntentFilter filter) { registerReceiver(receiver, filter); }

    @Override protected void onResume() {
        super.onResume(); resumed = true;
        if (awaitingAllFiles) {
            awaitingAllFiles = false;
            if (Build.VERSION.SDK_INT >= 30 && Environment.isExternalStorageManager()) approve(null, false, true);
            else status(getString(R.string.storage_not_enabled));
        }
        if (selected != null && selected.shared && Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) clearSelection();
        connect(false);
    }
    @Override protected void onPause() { resumed = false; super.onPause(); }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); connect(false); }

    private boolean matches(UsbAccessory accessory) {
        boolean legacy = "PocketPull".equals(accessory.getManufacturer()) && "PocketPull".equals(accessory.getModel());
        boolean branded = "Pocket Dimension".equals(accessory.getManufacturer()) && "Pocket Dimension".equals(accessory.getModel());
        return (legacy || branded) && "1".equals(accessory.getVersion());
    }

    private void connect(boolean askPermission) {
        if (descriptor != null || destroyed) return;
        UsbAccessory[] accessories = usb.getAccessoryList();
        if (accessories == null) return;
        for (UsbAccessory accessory : accessories) {
            if (!matches(accessory)) continue;
            if (!usb.hasPermission(accessory)) {
                connectionText.setText(getString(R.string.usb_approval_needed));
                if (askPermission) usb.requestPermission(accessory, PendingIntent.getBroadcast(this, 0,
                    new Intent(PERMISSION).setPackage(getPackageName()), PendingIntent.FLAG_IMMUTABLE));
                return;
            }
            try {
                ParcelFileDescriptor opened = usb.openAccessory(accessory);
                if (opened == null) throw new IOException("Could not open accessory");
                descriptor = opened;
                connectionText.setText(getString(R.string.usb_connected));
                new Thread(() -> serve(opened), "PocketDimension-USB").start();
            } catch (Exception error) { connectionText.setText(getString(R.string.usb_error, error.getMessage())); }
            return;
        }
    }

    private org.json.JSONObject greeting() throws Exception {
        return new org.json.JSONObject("{\"version\":5,\"app\":\"Pocket Dimension\",\"features\":[\"browse\",\"folders\",\"push\",\"cancel\",\"resume\",\"batch-ack\",\"secure\"]}")
            .put("device_id", deviceId).put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL);
    }
    private synchronized SecureAccessory security() throws Exception {
        if (secureOwner == null) {
            if (destroyed) throw new IOException("App closed");
            secureOwner = new SecureAccessory(getApplicationContext(), "dev.pocketdimension.usb", this::requestPairing);
        }
        return secureOwner;
    }
    private boolean requestPairing(SecureAccessory.PairingRequest request) throws Exception {
        CompletableFuture<Boolean> decision = new CompletableFuture<>(); pendingApproval = decision;
        runOnUiThread(() -> {
            KeyguardManager lock = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
            if (destroyed || !resumed || lock.isKeyguardLocked()) { decision.complete(false); return; }
            String message = getString(R.string.pair_message, request.computerName, formatFingerprint(request.computerFingerprint), formatFingerprint(request.phoneFingerprint));
            AlertDialog dialog = new AlertDialog.Builder(this).setTitle(R.string.pair_title).setMessage(message)
                .setNegativeButton(R.string.pair_deny, (d, w) -> decision.complete(false))
                .setPositiveButton(R.string.pair_allow, (d, w) -> decision.complete(!destroyed && resumed && !lock.isKeyguardLocked()))
                .setOnCancelListener(d -> decision.complete(false)).create();
            dialog.show();
            dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);
            new Handler(Looper.getMainLooper()).postDelayed(() -> { decision.complete(false); if (!destroyed && dialog.isShowing()) dialog.dismiss(); }, 80000);
        });
        try { return decision.get(80, TimeUnit.SECONDS); }
        catch (java.util.concurrent.TimeoutException timeout) { return false; }
        finally { if (pendingApproval == decision) pendingApproval = null; }
    }
    private static String formatFingerprint(String value) { return value.replaceAll("(.{8})(?!$)", "$1 "); }
    private void manageComputers() {
        metadataWorker.execute(() -> {
            try {
                SecureAccessory owner = security(); java.util.List<TrustPolicy.Grant> hosts = owner.trustedComputers();
                String phone = owner.fingerprint();
                runOnUiThread(() -> {
                    if (destroyed) return;
                    String[] labels = new String[hosts.size()];
                    for (int i=0;i<labels.length;i++) labels[i]=hosts.get(i).name + "\n" + getString(R.string.trust_until, java.text.DateFormat.getDateTimeInstance().format(new java.util.Date(hosts.get(i).expiresAt)));
                    AlertDialog.Builder builder = new AlertDialog.Builder(this).setTitle(R.string.trusted_computers)
                        .setNegativeButton(android.R.string.cancel, null)
                        .setNeutralButton(R.string.forget_all, (d,w) -> revokeComputer(owner, null));
                    if (hosts.isEmpty()) builder.setMessage(getString(R.string.no_trusted_computers) + "\n\n" + getString(R.string.phone_fingerprint) + "\n" + formatFingerprint(phone));
                    else builder.setItems(labels, (d,index) -> new AlertDialog.Builder(this).setTitle(R.string.forget_computer)
                        .setMessage(hosts.get(index).name + "\n\n" + formatFingerprint(hosts.get(index).fingerprint))
                        .setNegativeButton(android.R.string.cancel,null).setPositiveButton(R.string.forget_computer,(a,b) -> revokeComputer(owner,hosts.get(index).fingerprint)).show());
                    builder.show();
                });
            } catch (Exception error) { status(getString(R.string.security_error, error.getMessage())); }
        });
    }
    private void revokeComputer(SecureAccessory owner, String fingerprint) {
        clearSelection();
        metadataWorker.execute(() -> { try { if (fingerprint == null) owner.revokeAll(); else owner.revoke(fingerprint); status(getString(R.string.trust_revoked)); }
            catch (Exception error) { status(getString(R.string.security_error, error.getMessage())); } });
    }
    private void serve(ParcelFileDescriptor opened) {
        try (FileInputStream input = new FileInputStream(opened.getFileDescriptor());
             FileOutputStream output = new FileOutputStream(opened.getFileDescriptor())) {
            Wire outer = new Wire(input, output);
            while (!destroyed && descriptor == opened) {
                Wire.Frame frame = outer.receive();
                if (frame.kind == Wire.HELLO && frame.payload.length == 0) { outer.send(Wire.HELLO, greeting().toString()); continue; }
                if (frame.kind != Wire.SECURE || frame.payload.length != 0) { outer.send(Wire.ERROR, "Authenticated encrypted session required"); break; }
                SecureAccessory.Session session = security().accept(new TlsStream.Records() {
                    public byte[] receive() throws IOException { Wire.Frame next=outer.receive(); if(next.kind!=Wire.SECURE)throw new IOException("Expected encrypted USB records");return next.payload; }
                    public void send(byte[] bytes) throws IOException { outer.send(Wire.SECURE,bytes); }
                    public void abort() { try { opened.close(); } catch(IOException ignored) { } }
                });
                if (session == null) continue;
                threadSession.set(session);
                try { serveCommands(new Wire(session.input,session.output)); }
                catch (EOFException cleanClose) { /* TLS close_notify ends one logical client session. */ }
                finally { threadSession.remove(); session.close(); }
            }
        } catch (Exception error) { android.util.Log.w("PocketDimension", "USB session ended", error); status(getString(R.string.usb_session_ended)); }
        finally {
            try { opened.close(); } catch (IOException ignored) { }
            runOnUiThread(() -> { if (descriptor == opened) { descriptor=null; connectionText.setText(getString(R.string.usb_session_closed)); } });
        }
    }
    private void serveCommands(Wire wire) throws Exception {
            boolean greeted = false;
            while (!destroyed) {
                Wire.Frame frame = wire.receive();
                if (frame.kind == Wire.HELLO) {
                    wire.send(Wire.HELLO, greeting().toString());
                    greeted = true;
                    continue;
                }
                try {
                    if (!greeted) throw new IOException("Send HELLO first");
                    if (frame.kind == Wire.CANCEL) {
                        wire.send(Wire.END, "{\"cancelled\":true}");
                        continue;
                    }
                    Storage scope = selected;
                    if (frame.kind == Wire.LIST) {
                        wire.send(Wire.META, scope == null ? "{\"selected\":false}" : scope.json().toString());
                        continue;
                    }
                    if (scope == null) throw new IOException("Choose a file or folder in Pocket Dimension first");
                    checkApproval(scope);
                    JSONObject args = frame.payload.length == 0 ? new JSONObject() : new JSONObject(frame.text());
                    String path = args.optString("path", "");
                    SafePaths.parts(path);
                    if (frame.kind == Wire.STAT) wire.send(Wire.META, scope.resolve(path).json().toString());
                    else if (frame.kind == Wire.BROWSE) {
                        int count = 0;
                        for (Storage.Node child : scope.children(scope.resolve(path))) {
                            checkApproval(scope);
                            wire.send(Wire.META, child.json().toString()); count++;
                        }
                        wire.send(Wire.END, new JSONObject().put("entries", count).toString());
                    } else if (frame.kind == Wire.GET) transfer(wire, scope, scope.resolve(path), args.optBoolean("acknowledge", false), args);
                    else if (frame.kind == Wire.MKDIR) wire.send(Wire.META, scope.mkdir(path).json().toString());
                    else if (frame.kind == Wire.PUSH) {
                        if (args.optBoolean("resume", false)) receiveResumableUpload(wire, scope, path, args);
                        else receiveUpload(wire, scope, path, args.getLong("size"), batchFrames(args));
                    }
                    else throw new IOException("Unknown command");
                } catch (OperationCancelled cancelled) {
                    wire.send(Wire.END, "{\"cancelled\":true}");
                    status(getString(R.string.transfer_cancelled));
                } catch (Exception error) {
                    wire.send(Wire.ERROR, "Operation stopped: " + error.getMessage());
                    status(getString(R.string.operation_error, error.getMessage()));
                }
            }
    }

    private static final class OperationCancelled extends IOException { }

    private static final int MAX_BATCH_FRAMES = 16;
    private int batchFrames(JSONObject request) throws Exception {
        int frames = request.optInt("batch_frames", 0);
        if (frames < 0 || frames > MAX_BATCH_FRAMES) throw new IOException("Invalid batch size");
        return frames;
    }
    private void downloadAcknowledgment(Wire wire, long total, boolean batched) throws Exception {
        if (batched) wire.send(Wire.META, new JSONObject().put("batch", true).put("bytes", total).toString());
        Wire.Frame response = wire.receive();
        if (response.kind == Wire.CANCEL) throw new OperationCancelled();
        if (response.kind != Wire.META || new JSONObject(response.text()).getLong("bytes") != total)
            throw new IOException("Invalid download acknowledgment");
    }
    private Wire.Frame receiveUploadBatch(Wire wire, int frames, long total) throws Exception {
        if (frames == 0) return wire.receive();
        // Consume a complete bounded batch before disk writes or a response. This leaves
        // no queued DATA behind when a write fails or a sharing approval is revoked.
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(frames * Wire.MAX);
        int count = 0;
        while (true) {
            Wire.Frame frame = wire.receive();
            if (frame.kind == Wire.DATA) {
                if (frame.payload.length == 0 || count >= frames) throw new IOException("Upload batch limit exceeded");
                bytes.write(frame.payload); count++;
            } else if (frame.kind == Wire.META && count > 0) {
                JSONObject marker = new JSONObject(frame.text());
                if (!marker.optBoolean("batch", false) || marker.getLong("bytes") != total + bytes.size())
                    throw new IOException("Invalid upload batch boundary");
                return new Wire.Frame(Wire.DATA, bytes.toByteArray());
            } else if (frame.kind == Wire.CANCEL || count == 0) return frame;
            else throw new IOException("Missing upload batch boundary");
        }
    }

    private void checkApproval(Storage scope) throws IOException {
        SecureAccessory.Session session = threadSession.get();
        if (session != null) session.requireAuthorized();
        if (selected != scope || destroyed) throw new IOException("Sharing approval revoked");
        scope.check();
    }
    private static String hex(byte[] digest) {
        StringBuilder hash = new StringBuilder();
        for (byte value : digest) hash.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return hash.toString();
    }
    private void transfer(Wire wire, Storage scope, Storage.Node file, boolean acknowledge) throws Exception {
        transfer(wire, scope, file, acknowledge, new JSONObject());
    }
    private void transfer(Wire wire, Storage scope, Storage.Node file, boolean acknowledge, JSONObject request) throws Exception {
        try (InputStream stream = scope.input(file)) {
            int frames = batchFrames(request);
            if (frames > 0 && !acknowledge) throw new IOException("Batch downloads require acknowledgments");
            long offset = request.optLong("offset", 0);
            if (offset < 0 || (file.size >= 0 && offset > file.size)) throw new IOException("RESUME_MISMATCH: invalid offset");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            MessageDigest prefix = MessageDigest.getInstance("SHA-256");
            byte[] prefixBuffer = new byte[Wire.MAX];
            long remaining = offset;
            while (remaining > 0) {
                checkApproval(scope);
                int n = stream.read(prefixBuffer, 0, (int)Math.min(prefixBuffer.length, remaining));
                if (n < 0) throw new IOException("RESUME_MISMATCH: source shorter than retained prefix");
                digest.update(prefixBuffer, 0, n); prefix.update(prefixBuffer, 0, n); remaining -= n;
            }
            if (request.has("prefix_sha256") && !hex(prefix.digest()).equals(request.getString("prefix_sha256")))
                throw new IOException("RESUME_MISMATCH: source prefix changed; restart required");
            wire.send(Wire.META, file.json().put("offset", offset).put("batch_frames", frames).toString());
            byte[] buffer = new byte[Wire.MAX];
            long total = offset, lastUpdate = 0;
            status(getString(R.string.sending, file.name));
            int count, pendingFrames = 0;
            while ((count = stream.read(buffer)) != -1) {
                checkApproval(scope);
                if (count == 0) continue;
                digest.update(buffer, 0, count);
                wire.send(Wire.DATA, count == buffer.length ? buffer : Arrays.copyOf(buffer, count));
                total += count;
                if (acknowledge && (frames == 0 || ++pendingFrames == frames)) {
                    downloadAcknowledgment(wire, total, frames > 0); pendingFrames = 0;
                }
                long now = SystemClock.elapsedRealtime();
                if (now - lastUpdate > 250) { status(byteCount(R.plurals.sent_progress, total, total)); lastUpdate = now; }
            }
            if (acknowledge && pendingFrames > 0) downloadAcknowledgment(wire, total, true);
            checkApproval(scope);
            wire.send(Wire.END, new JSONObject().put("bytes", total).put("sha256", hex(digest.digest())).toString());
            status(byteCount(R.plurals.sent_complete, total, total));
        }
    }
    private void receiveResumableUpload(Wire wire, Storage scope, String path, JSONObject request) throws Exception {
        int frames = batchFrames(request);
        long expected = request.getLong("size");
        String checksum = request.getString("sha256");
        if (expected < 0 || !checksum.matches("[0-9a-f]{64}")) throw new IOException("Invalid resume identity");
        // Verify parent exists before storing any upload data, but do not create final output yet.
        String[] parts = SafePaths.parts(path);
        if (parts.length == 0) throw new IOException("Destination filename required");
        String parent = String.join("/", Arrays.copyOf(parts, parts.length - 1));
        Storage.Node folder = scope.resolve(parent);
        for (Storage.Node child : scope.children(folder)) if (child.name.equals(parts[parts.length - 1])) throw new IOException("Destination already exists");
        File cache = new File(getCacheDir(), "uploads");
        if (!cache.exists() && !cache.mkdirs()) throw new IOException("Could not create resume cache");
        File[] old = cache.listFiles();
        if (old != null) for (File file : old) if (System.currentTimeMillis() - file.lastModified() > 7L * 24 * 60 * 60 * 1000) file.delete();
        String key = hex(MessageDigest.getInstance("SHA-256").digest((scope.json().getString("scope_id") + "\n" + path + "\n" + expected + "\n" + checksum).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        File partial = new File(cache, key + ".part");
        if (partial.length() > expected) partial.delete();
        if (expected - partial.length() > cache.getUsableSpace()) throw new IOException("Not enough app storage for resumable upload");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        if (partial.exists()) try (InputStream input = new FileInputStream(partial)) {
            byte[] buffer = new byte[Wire.MAX]; int n;
            while ((n = input.read(buffer)) != -1) { checkApproval(scope); digest.update(buffer, 0, n); }
        }
        MessageDigest prefix = MessageDigest.getInstance("SHA-256");
        if (partial.exists()) try (InputStream input = new FileInputStream(partial)) {
            byte[] buffer = new byte[Wire.MAX]; int n;
            while ((n = input.read(buffer)) != -1) { checkApproval(scope); prefix.update(buffer, 0, n); }
        }
        long total = partial.length(), lastUpdate = 0;
        boolean discard = false, complete = false;
        try (OutputStream out = new FileOutputStream(partial, true)) {
            wire.send(Wire.META, new JSONObject().put("ready", true).put("offset", total).put("prefix_sha256", hex(prefix.digest())).put("batch_frames", frames).toString());
            while (true) {
                Wire.Frame frame = receiveUploadBatch(wire, frames, total); checkApproval(scope);
                if (frame.kind == Wire.CANCEL) {
                    if (frame.payload.length > 0) discard = new JSONObject(frame.text()).optBoolean("discard", false);
                    throw new OperationCancelled();
                }
                if (frame.kind == Wire.DATA) {
                    if (frame.payload.length > expected - total) { discard = true; throw new IOException("Upload exceeds declared size"); }
                    out.write(frame.payload); digest.update(frame.payload); total += frame.payload.length;
                    wire.send(Wire.META, new JSONObject().put("bytes", total).toString());
                    long now = SystemClock.elapsedRealtime();
                    if (now - lastUpdate > 250) { status(byteCount(R.plurals.receiving, total, total)); lastUpdate = now; }
                } else if (frame.kind == Wire.END) {
                    JSONObject end = new JSONObject(frame.text());
                    if (total != expected || end.getLong("bytes") != total || !checksum.equals(end.getString("sha256")) || !checksum.equals(hex(digest.digest()))) {
                        discard = true; throw new IOException("Upload checksum mismatch");
                    }
                    out.close();
                    try (Storage.Upload upload = scope.upload(path); InputStream input = new FileInputStream(partial)) {
                        byte[] buffer = new byte[Wire.MAX]; int n;
                        while ((n = input.read(buffer)) != -1) { checkApproval(scope); upload.out.write(buffer, 0, n); }
                        upload.out.close();
                        MessageDigest stored = MessageDigest.getInstance("SHA-256"); long count = 0;
                        try (InputStream verify = scope.input(upload.node)) {
                            while ((n = verify.read(buffer)) != -1) { checkApproval(scope); stored.update(buffer, 0, n); count += n; }
                        }
                        if (count != total || !checksum.equals(hex(stored.digest()))) throw new IOException("Stored file verification failed");
                        upload.committed = true;
                    }
                    complete = true;
                    wire.send(Wire.END, new JSONObject().put("bytes", total).put("sha256", checksum).toString());
                    status(byteCount(R.plurals.received, total, total)); return;
                } else throw new IOException("Invalid resume upload frame");
            }
        } finally { if (discard || complete) partial.delete(); }
    }
    private void receiveUpload(Wire wire, Storage scope, String path, long expected, int frames) throws Exception {
        if (expected < 0) throw new IOException("Invalid upload size");
        try (Storage.Upload upload = scope.upload(path)) {
            wire.send(Wire.META, new JSONObject().put("ready", true).put("batch_frames", frames).toString());
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long total = 0, lastUpdate = 0;
            while (true) {
                Wire.Frame frame = receiveUploadBatch(wire, frames, total);
                checkApproval(scope);
                if (frame.kind == Wire.CANCEL) throw new OperationCancelled();
                if (frame.kind == Wire.DATA) {
                    if (frame.payload.length > expected - total) throw new IOException("Upload exceeds declared size");
                    upload.out.write(frame.payload);
                    digest.update(frame.payload); total += frame.payload.length;
                    wire.send(Wire.META, new JSONObject().put("bytes", total).toString());
                    long now = SystemClock.elapsedRealtime();
                    if (now - lastUpdate > 250) { status(byteCount(R.plurals.receiving, total, total)); lastUpdate = now; }
                } else if (frame.kind == Wire.END) {
                    JSONObject end = new JSONObject(frame.text());
                    String checksum = hex(digest.digest());
                    if (total != expected || end.getLong("bytes") != total || !checksum.equals(end.getString("sha256")))
                        throw new IOException("Upload checksum or byte count mismatch");
                    upload.out.close();
                    MessageDigest stored = MessageDigest.getInstance("SHA-256");
                    long storedBytes = 0;
                    try (InputStream input = scope.input(upload.node)) {
                        byte[] buffer = new byte[Wire.MAX]; int n;
                        while ((n = input.read(buffer)) != -1) { checkApproval(scope); stored.update(buffer, 0, n); storedBytes += n; }
                    }
                    checkApproval(scope);
                    if (storedBytes != total || !checksum.equals(hex(stored.digest()))) throw new IOException("Stored file verification failed");
                    upload.committed = true;
                    wire.send(Wire.END, new JSONObject().put("bytes", total).put("sha256", checksum).toString());
                    status(byteCount(R.plurals.received, total, total));
                    return;
                } else throw new IOException("Upload cancelled or invalid frame");
            }
        }
    }

    private void clearSelection() {
        selectionGeneration++; selected = null;
        fileText.setText(getString(R.string.file_unselected));
        transferText.setText(getString(R.string.file_cleared));
    }
    private void chooseFolder() {
        startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION), PICK_FOLDER);
    }
    private void shareStorage() {
        if (Build.VERSION.SDK_INT < 30) { chooseFolder(); return; }
        if (Environment.isExternalStorageManager()) { approve(null, false, true); return; }
        awaitingAllFiles = true;
        try { startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName()))); }
        catch (ActivityNotFoundException error) {
            try { startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)); }
            catch (ActivityNotFoundException missing) { awaitingAllFiles = false; status(getString(R.string.storage_not_enabled)); }
        }
    }
    private void approve(Uri uri, boolean directory, boolean shared) {
        int generation = ++selectionGeneration;
        fileText.setText(getString(R.string.file_loading));
        metadataWorker.execute(() -> {
            try {
                Storage scope = shared ? Storage.shared(getContentResolver()) : Storage.document(getContentResolver(), uri, directory);
                runOnUiThread(() -> {
                    if (destroyed || generation != selectionGeneration) return;
                    selected = scope;
                    Storage.Node file = scope.root;
                    fileText.setText(file.directory ? getString(R.string.folder_selected, shared ? getString(R.string.shared_storage) : file.name)
                        : file.size >= 0 ? byteCount(R.plurals.file_known_size, file.size, file.name, file.size) : getString(R.string.file_unknown_size, file.name));
                    transferText.setText(getString(file.directory ? R.string.folder_approved : R.string.file_approved));
                });
            } catch (Exception error) { status(getString(R.string.file_error, error.getMessage())); }
        });
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if ((request != PICK && request != PICK_FOLDER) || result != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        if (!"content".equals(uri.getScheme())) { status(getString(R.string.file_pick_prompt)); return; }
        approve(uri, request == PICK_FOLDER, false);
    }

    private String byteCount(int resource, long count, Object... args) {
        return getResources().getQuantityString(resource, (int) Math.min(count, Integer.MAX_VALUE), args);
    }

    private void status(String text) { runOnUiThread(() -> { if (!destroyed) transferText.setText(text); }); }
    private void closeConnection() {
        CompletableFuture<Boolean> approval = pendingApproval; if (approval != null) approval.complete(false);
        SecureAccessory owner = secureOwner; if (owner != null) owner.invalidateSessions();
        ParcelFileDescriptor current = descriptor;
        descriptor = null;
        if (current != null) try { current.close(); } catch (IOException ignored) { }
    }
    @Override protected void onDestroy() {
        destroyed = true;
        SecureAccessory owner = secureOwner; if (owner != null) owner.close();
        selected = null;
        closeConnection();
        metadataWorker.shutdownNow();
        unregisterReceiver(receiver);
        super.onDestroy();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value); view.setTextSize(size); view.setTextColor(color);
        view.setPadding(0, dp(7), 0, dp(7));
        return view;
    }
    private LinearLayout card(LinearLayout parent) {
        LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(22), dp(16), dp(22), dp(20));
        GradientDrawable background = new GradientDrawable();
        background.setColor(getColor(R.color.surface)); background.setCornerRadius(dp(22));
        background.setStroke(dp(1), getColor(R.color.border)); card.setBackground(background);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2); layout.topMargin = dp(20);
        parent.addView(card, layout); return card;
    }
    private void button(LinearLayout parent, String label, Runnable action) {
        Button button = new Button(this); button.setText(label); button.setAllCaps(false);
        boolean primary = label.equals(getString(R.string.connect_button)) || label.equals(getString(R.string.folder_button));
        button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(getColor(primary ? R.color.accent : R.color.accent_soft)));
        button.setTextColor(getColor(primary ? R.color.on_accent : R.color.ink));
        button.setOnClickListener(view -> action.run()); parent.addView(button, new LinearLayout.LayoutParams(-1, dp(56)));
    }
    private void buildUi() {
        int ink = getColor(R.color.ink), muted = getColor(R.color.muted);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        LinearLayout column = new LinearLayout(this); column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(24), dp(24), dp(24), dp(32)); scroll.addView(column);
        if (Build.VERSION.SDK_INT >= 30) scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom); return insets;
        });
        // WindowInsets.Type is available from API 30; use legacy insets on older phones.
        if (Build.VERSION.SDK_INT < 30) scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets;
        });
        LinearLayout identity = new LinearLayout(this); identity.setGravity(Gravity.CENTER_VERTICAL);
        ImageView portal = new ImageView(this); portal.setImageResource(R.drawable.ic_portal); portal.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams mark = new LinearLayout.LayoutParams(dp(48), dp(64)); mark.rightMargin = dp(14); identity.addView(portal, mark);
        TextView brand = text(getString(R.string.brand), 12, getColor(R.color.accent)); brand.setLetterSpacing(0.1f); brand.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        identity.addView(brand, new LinearLayout.LayoutParams(0, -2, 1)); column.addView(identity);
        TextView heading = text(getString(R.string.heading), 40, ink);
        heading.setTypeface(Typeface.DEFAULT, Typeface.BOLD); column.addView(heading);
        column.addView(text(getString(R.string.intro), 17, muted));
        LinearLayout connection = card(column);
        connection.addView(text(getString(R.string.connect_heading), 12, muted));
        connectionText = text(getString(R.string.usb_waiting), 18, ink); connection.addView(connectionText);
        button(connection, getString(R.string.connect_button), () -> connect(true));
        button(connection, getString(R.string.trusted_computers), this::manageComputers);
        LinearLayout file = card(column);
        file.addView(text(getString(R.string.file_heading), 12, muted));
        fileText = text(getString(R.string.file_unselected), 18, ink); file.addView(fileText);
        button(file, getString(R.string.choose_button), () -> startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT)
            .setType("*/*").addCategory(Intent.CATEGORY_OPENABLE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), PICK));
        button(file, getString(R.string.folder_button), this::chooseFolder);
        file.addView(text(getString(R.string.folder_scope_help), 14, muted));
        button(file, getString(R.string.all_files_button), this::shareStorage);
        file.addView(text(getString(R.string.all_files_help), 14, muted));
        button(file, getString(R.string.clear_button), this::clearSelection);
        LinearLayout transfer = card(column);
        transfer.addView(text(getString(R.string.pull_heading), 12, muted));
        TextView command = text(getString(R.string.pull_command), 16, ink); command.setTypeface(Typeface.MONOSPACE); transfer.addView(command);
        transferText = text(getString(R.string.pull_help), 15, muted);
        transfer.addView(transferText);
        setContentView(scroll);
    }
}
