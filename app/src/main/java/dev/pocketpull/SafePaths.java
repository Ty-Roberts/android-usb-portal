package dev.pocketpull;

import java.io.IOException;

/** Platform-independent path validation shared by document and filesystem scopes. */
final class SafePaths {
    static String name(String value) throws IOException {
        if (value == null || value.isEmpty() || value.equals(".") || value.equals("..")
                || value.contains("/") || value.contains("\\") || value.length() > 512)
            throw new IOException("Invalid path component");
        for (int i = 0; i < value.length(); i++)
            if (value.charAt(i) < 32 || value.charAt(i) == 127) throw new IOException("Invalid filename");
        return value;
    }

    static String[] parts(String value) throws IOException {
        if (value == null || value.length() > 8192) throw new IOException("Invalid relative path");
        if (value.isEmpty()) return new String[0];
        String[] result = value.split("/", -1);
        if (result.length > 128) throw new IOException("Path nesting limit exceeded");
        for (String part : result) name(part);
        return result;
    }
}
