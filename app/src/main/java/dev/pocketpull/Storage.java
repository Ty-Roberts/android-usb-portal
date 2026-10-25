package dev.pocketpull;

import android.content.ContentResolver;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.os.Build;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;
import org.json.JSONObject;
import java.io.*;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.util.*;

/** A session-scoped document tree, selected file, or explicitly approved shared storage. */
final class Storage {
    final ContentResolver resolver;
    final Node root;
    final Uri tree;
    final boolean shared;
    static final String DIR = DocumentsContract.Document.MIME_TYPE_DIR;
    static final int LIMIT = 100000;

    static final class Node {
        final Uri uri;
        final File file;
        final String name;
        final boolean directory, writable;
        final long size, modified;
        Node(Uri uri, File file, String name, boolean directory, boolean writable, long size) {
            this(uri, file, name, directory, writable, size, file == null ? 0 : file.lastModified());
        }
        Node(Uri uri, File file, String name, boolean directory, boolean writable, long size, long modified) {
            this.modified = modified;
            this.uri = uri; this.file = file; this.name = name;
            this.directory = directory; this.writable = writable; this.size = size;
        }
        JSONObject json() throws Exception {
            return new JSONObject().put("name", name).put("type", directory ? "directory" : "file")
                .put("modified", modified > 0 ? modified : JSONObject.NULL).put("writable", writable).put("size", directory || size < 0 ? JSONObject.NULL : size);
        }
    }

    private Storage(ContentResolver resolver, Node root, Uri tree, boolean shared) {
        this.resolver = resolver; this.root = root; this.tree = tree; this.shared = shared;
    }
    static Storage document(ContentResolver resolver, Uri uri, boolean directory) throws Exception {
        Uri actual = directory ? DocumentsContract.buildDocumentUriUsingTree(uri, DocumentsContract.getTreeDocumentId(uri)) : uri;
        Storage storage = new Storage(resolver, null, directory ? uri : null, false);
        return new Storage(resolver, storage.info(actual, directory), directory ? uri : null, false);
    }
    static Storage shared(ContentResolver resolver) throws Exception {
        if (Build.VERSION.SDK_INT < 30 || !Environment.isExternalStorageManager()) throw new IOException("All files access is not enabled");
        File root = Environment.getExternalStorageDirectory().getCanonicalFile();
        return new Storage(resolver, new Node(null, root, "Shared storage", true, root.canWrite(), -1), null, true);
    }
    void check() throws IOException {
        if (shared && (Build.VERSION.SDK_INT < 30 || !Environment.isExternalStorageManager()))
            throw new IOException("All files access was revoked");
    }
    JSONObject json() throws Exception {
        check();
        String identity = root.file != null ? root.file.getCanonicalPath() : root.uri.toString();
        byte[] digest = java.security.MessageDigest.getInstance("SHA-256").digest(identity.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        StringBuilder scopeId = new StringBuilder();
        for (byte value : digest) scopeId.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return root.json().put("selected", true).put("scope_id", scopeId.toString()).put("scope", shared ? "shared-storage" : tree != null ? "folder" : "file");
    }
    private static Node fileNode(File file) throws IOException {
        if (!file.exists()) throw new FileNotFoundException("File no longer exists");
        return new Node(null, file, file.getName(), file.isDirectory(), file.canWrite(), file.isFile() ? file.length() : -1);
    }
    private Node info(Uri uri, boolean directory) throws Exception {
        String name = directory ? "Shared folder" : "Selected file";
        long size = -1, modified = 0; boolean writable = false;
        String[] columns = {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        try (Cursor cursor = resolver.query(uri, columns, null, null, null)) {
            if (cursor == null || !cursor.moveToFirst()) throw new IOException("Could not read document");
            int n = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME), s = cursor.getColumnIndex(OpenableColumns.SIZE);
            if (n >= 0 && !cursor.isNull(n)) name = cursor.getString(n);
            if (s >= 0 && !cursor.isNull(s)) size = cursor.getLong(s);
        }
        if (tree != null) {
            try (Cursor cursor = resolver.query(uri, new String[]{DocumentsContract.Document.COLUMN_FLAGS, DocumentsContract.Document.COLUMN_LAST_MODIFIED}, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    int flags = cursor.getInt(0);
                    if (!cursor.isNull(1)) modified = cursor.getLong(1);
                    writable = (flags & (DocumentsContract.Document.FLAG_SUPPORTS_WRITE | DocumentsContract.Document.FLAG_DIR_SUPPORTS_CREATE)) != 0;
                }
            }
        }
        return new Node(uri, null, name, directory, writable, size, modified);
    }
    List<Node> children(Node parent) throws Exception {
        check();
        if (!parent.directory) throw new IOException("Choose a folder on the phone to browse");
        List<Node> nodes = new ArrayList<>();
        Set<String> names = new HashSet<>();
        if (parent.file != null) {
            File[] files = parent.file.listFiles();
            if (files == null) throw new IOException("Cannot list this Android location");
            for (File file : files) {
                // Symlinks are never followed, even if their target is inside the scope.
                if (Files.isSymbolicLink(file.toPath())) continue;
                try { SafePaths.name(file.getName()); } catch (IOException ignored) { continue; }
                nodes.add(fileNode(file));
                if (nodes.size() > LIMIT) throw new IOException("Directory entry limit exceeded");
            }
        } else {
            Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getDocumentId(parent.uri));
            String[] columns = {DocumentsContract.Document.COLUMN_DOCUMENT_ID, OpenableColumns.DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE, OpenableColumns.SIZE, DocumentsContract.Document.COLUMN_FLAGS, DocumentsContract.Document.COLUMN_LAST_MODIFIED};
            try (Cursor cursor = resolver.query(children, columns, null, null, null)) {
                if (cursor == null) throw new IOException("Cannot list folder");
                while (cursor.moveToNext()) {
                    String name = cursor.getString(1);
                    SafePaths.name(name);
                    if (!names.add(name)) throw new IOException("Provider returned duplicate filenames");
                    int flags = cursor.getInt(4);
                    nodes.add(new Node(DocumentsContract.buildDocumentUriUsingTree(tree, cursor.getString(0)), null, name,
                        DIR.equals(cursor.getString(2)), (flags & (DocumentsContract.Document.FLAG_SUPPORTS_WRITE | DocumentsContract.Document.FLAG_DIR_SUPPORTS_CREATE)) != 0,
                        cursor.isNull(3) ? -1 : cursor.getLong(3), cursor.isNull(5) ? 0 : cursor.getLong(5)));
                    if (nodes.size() > LIMIT) throw new IOException("Directory entry limit exceeded");
                }
            }
        }
        nodes.sort(Comparator.comparing((Node n) -> !n.directory).thenComparing(n -> n.name));
        return nodes;
    }
    private Node child(Node parent, String name) throws Exception {
        SafePaths.name(name);
        for (Node node : children(parent)) if (name.equals(node.name)) return node;
        return null;
    }
    Node resolve(String path) throws Exception {
        check();
        String[] parts = SafePaths.parts(path);
        Node current = root;
        if (shared) {
            if (parts.length == 0) return root;
            File target = root.file;
            for (String part : parts) {
                target = new File(target, part);
                if (Files.isSymbolicLink(target.toPath())) throw new IOException("Symbolic links are not shared");
            }
            String base = root.file.getCanonicalPath(), actual = target.getCanonicalPath();
            if (!actual.equals(base) && !actual.startsWith(base + File.separator)) throw new IOException("Path outside shared storage");
            return fileNode(target);
        }
        for (String part : parts) {
            Node next = child(current, part);
            if (next == null) throw new FileNotFoundException("No such file: " + part);
            current = next;
        }
        return current;
    }
    InputStream input(Node node) throws Exception {
        check();
        if (node.directory) throw new IOException("Use directory download for folders");
        InputStream input = node.file != null ? new FileInputStream(node.file) : resolver.openInputStream(node.uri);
        if (input == null) throw new IOException("Could not open selected file");
        return input;
    }
    private Node parent(String[] parts) throws Exception {
        if (parts.length == 0) throw new IOException("Destination filename is required");
        String path = String.join("/", Arrays.copyOf(parts, parts.length - 1));
        Node node = resolve(path);
        if (!node.directory) throw new IOException("Upload requires a shared folder");
        return node;
    }
    Node mkdir(String path) throws Exception {
        String[] parts = SafePaths.parts(path);
        Node parent = parent(parts);
        String name = parts[parts.length - 1];
        Node existing = child(parent, name);
        if (existing != null) {
            if (!existing.directory) throw new IOException("A file already exists at destination");
            return existing;
        }
        if (parent.file != null) {
            File dir = new File(parent.file, name);
            if (!dir.mkdir()) throw new IOException("Could not create directory");
            return fileNode(dir);
        }
        Uri uri = DocumentsContract.createDocument(resolver, parent.uri, DIR, name);
        if (uri == null) throw new IOException("Could not create folder");
        Node result = info(uri, true);
        if (!result.name.equals(name)) { DocumentsContract.deleteDocument(resolver, uri); throw new IOException("Provider renamed destination; operation stopped"); }
        return result;
    }
    Upload upload(String path) throws Exception {
        String[] parts = SafePaths.parts(path);
        Node parent = parent(parts);
        String name = parts[parts.length - 1];
        if (child(parent, name) != null) throw new IOException("Destination already exists; uploads never overwrite");
        Node created;
        OutputStream out;
        if (parent.file != null) {
            File file = new File(parent.file, name);
            out = Files.newOutputStream(file.toPath(), StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
            created = fileNode(file);
        } else {
            Uri uri = DocumentsContract.createDocument(resolver, parent.uri, "application/octet-stream", name);
            if (uri == null) throw new IOException("Could not create upload");
            try {
                created = info(uri, false);
                if (!created.name.equals(name)) throw new IOException("Provider renamed destination; operation stopped");
                out = resolver.openOutputStream(uri, "wt");
                if (out == null) throw new IOException("Could not open upload");
            } catch (Exception error) { DocumentsContract.deleteDocument(resolver, uri); throw error; }
        }
        return new Upload(created, out);
    }
    final class Upload implements AutoCloseable {
        final Node node;
        final OutputStream out;
        boolean committed;
        Upload(Node node, OutputStream out) { this.node = node; this.out = out; }
        @Override public void close() throws Exception {
            try { out.close(); }
            finally {
                if (!committed) {
                    if (node.file != null) Files.deleteIfExists(node.file.toPath());
                    else if (!DocumentsContract.deleteDocument(resolver, node.uri)) throw new IOException("Could not remove incomplete upload");
                }
            }
        }
    }
}
