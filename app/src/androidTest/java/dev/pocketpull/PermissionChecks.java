package dev.pocketpull;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.provider.DocumentsContract;
import java.io.*;
import java.lang.reflect.*;
import java.util.UUID;

/** Opt-in real folder-picker check; requires the existing all-files grant for fixture cleanup. */
final class PermissionChecks {
    static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    static void run(StorageChecks test) throws Exception {
        require(Environment.isExternalStorageManager(), "Existing all-files access required; test will not grant it");
        String name = "PocketDimension-permission-check-" + UUID.randomUUID();
        File root = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), name);
        require(root.mkdirs(), "Cannot create generated test folder");
        MainActivity activity = null;
        try {
            activity = (MainActivity)test.startActivitySync(new Intent(test.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            MainActivity target = activity;
            Field selected = MainActivity.class.getDeclaredField("selected"); selected.setAccessible(true);
            Uri initial = DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Download/" + name);
            test.runOnMainSync(() -> target.startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
                .putExtra(DocumentsContract.EXTRA_INITIAL_URI, initial)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION), 11));
            Bundle state = new Bundle(); state.putString("stream", "PICKER_READY " + name + "\n"); test.sendStatus(0,state);
            long deadline = SystemClock.elapsedRealtime() + 4 * 60 * 1000;
            Storage scope;
            while ((scope=(Storage)selected.get(activity)) == null) {
                if (SystemClock.elapsedRealtime() > deadline) throw new IOException("Folder picker approval timed out");
                SystemClock.sleep(100);
            }
            require(scope.tree != null && scope.root.name.equals(name), "Picker returned another scope; refusing file operations");
            byte[] content = "Pocket Dimension SAF permission fixture\n".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            scope.mkdir("nested");
            try (Storage.Upload upload = scope.upload("nested/check.txt")) { upload.out.write(content); upload.out.close(); upload.committed=true; }
            try (InputStream input = scope.input(scope.resolve("nested/check.txt"))) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer=new byte[1024];int n;
                while((n=input.read(buffer))!=-1)bytes.write(buffer,0,n);
                require(java.util.Arrays.equals(content,bytes.toByteArray()),"SAF provider roundtrip differs");
            }
            require(scope.children(scope.resolve("nested")).size()==1,"SAF listing differs");
            boolean blocked=false;try{scope.resolve("../outside");}catch(IOException expected){blocked=true;}
            require(blocked,"SAF path containment failed");
            test.runOnMainSync(() -> {try{Method clear=MainActivity.class.getDeclaredMethod("clearSelection");clear.setAccessible(true);clear.invoke(target);}catch(Exception error){throw new RuntimeException(error);}});
            require(selected.get(activity)==null,"Clear selection failed");
            state=new Bundle();state.putString("stream","PASS system folder-picker provider listing, write/read roundtrip, containment, and clear selection\n");test.sendStatus(0,state);
        } finally {
            if(activity!=null){MainActivity target=activity;test.runOnMainSync(target::finishAndRemoveTask);}
            remove(root);
        }
    }
    static void remove(File file){File[] children=file.listFiles();if(children!=null)for(File child:children)remove(child);file.delete();}
}
