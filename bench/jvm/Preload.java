import java.io.*;
import java.lang.reflect.Method;
import java.nio.file.*;
import java.util.*;

/** The ceiling: a JVM that loads the tool's classes, is checkpointed there, and on restore
 *  calls the tool's main with the arguments baked in. No client, no daemon. */
public class Preload {
    public static void main(String[] a) throws Exception {
        String list = a[0], mainClass = a[1];
        String[] toolArgs = Arrays.copyOfRange(a, 2, a.length);
        ClassLoader cl = Preload.class.getClassLoader();
        int loaded = 0;
        for (String name : Files.readAllLines(Path.of(list))) {
            try { Class.forName(name, Boolean.getBoolean("j0.init"), cl); loaded++; } catch (Throwable t) { }
        }
        System.err.println("preloaded " + loaded);
        jdk.crac.Core.checkpointRestore();
        Class.forName(mainClass, true, cl).getMethod("main", String[].class).invoke(null, (Object) toolArgs);
    }
}
