/** The kindest case for a snapshot: javac's main returns, so it can run many times
 *  before the checkpoint, and the code is compiled by then. */
public class WarmJavac {
    public static void main(String[] a) throws Exception {
        int warm = Integer.parseInt(a[0]);
        String[] args = {"-d", "out", "Hello.java"};
        for (int i = 0; i < warm; i++) com.sun.tools.javac.Main.compile(args);
        jdk.crac.Core.checkpointRestore();
        System.exit(com.sun.tools.javac.Main.compile(args));
    }
}
