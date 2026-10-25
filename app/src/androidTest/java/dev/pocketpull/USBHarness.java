package dev.pocketpull;

import android.app.*;
import android.content.Intent;
import android.os.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.UUID;

/** Opt-in physical USB acceptance fixture. Exposes only generated app-owned files. */
final class USBHarness {
    static void run(StorageChecks test, boolean benchmark) throws Exception {
        File root = new File(test.getTargetContext().getExternalFilesDir(null), "PocketDimension-USB-check-" + UUID.randomUUID());
        if (!root.mkdirs()) throw new IOException("Cannot create USB fixture");
        MainActivity activity = null;
        try {
            new File(root, "nested").mkdir();
            new File(root, "nested/empty").createNewFile();
            try (OutputStream out = new FileOutputStream(new File(root, "large.bin"))) {
                byte[] bytes = new byte[65536];
                for (int i = 0; i < bytes.length; i++) bytes[i] = (byte)(i % 251);
                for (int i = 0; i < 192; i++) out.write(bytes);
            }
            Constructor<Storage> constructor = Storage.class.getDeclaredConstructor(android.content.ContentResolver.class, Storage.Node.class, android.net.Uri.class, boolean.class);
            constructor.setAccessible(true);
            Storage scope = constructor.newInstance(test.getTargetContext().getContentResolver(), new Storage.Node(null, root, root.getName(), true, true, -1), null, benchmark && Environment.isExternalStorageManager());
            activity = (MainActivity)test.startActivitySync(new Intent(test.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            Field selected = MainActivity.class.getDeclaredField("selected"); selected.setAccessible(true); selected.set(activity, scope);
            Bundle state = new Bundle(); state.putString("stream", "USB_READY " + root.getName() + "\n"); test.sendStatus(0, state);
            long deadline = SystemClock.elapsedRealtime() + (benchmark ? 45 : 15) * 60 * 1000;
            while (!new File(root, "__finish__").isDirectory()) {
                if (SystemClock.elapsedRealtime() > deadline) throw new IOException("USB fixture timed out");
                SystemClock.sleep(250);
            }
        } finally {
            if (activity != null) {
                MainActivity target = activity;
                test.runOnMainSync(() -> { try {
                    Method clear = MainActivity.class.getDeclaredMethod("clearSelection"); clear.setAccessible(true); clear.invoke(target);
                    target.finish();
                } catch (Exception error) { throw new RuntimeException(error); } });
            }
            remove(root);
        }
    }
    private static void remove(File file) { File[] children = file.listFiles(); if (children != null) for (File child : children) remove(child); file.delete(); }
}
