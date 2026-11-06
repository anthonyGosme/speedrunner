"""The panel: where each CLI comes from, and commands with no network and no
side effect outside their working directory."""
J = "/tools"
JAR = lambda name, *a: ["java", "-jar", f"{J}/{name}/{name}.jar", *a]
MC = "https://repo1.maven.org/maven2"
AP = "https://archive.apache.org/dist"

# name: (family, source, [(label, argv)])
# source: ("maven", group/artifact path, suffix) latest release of a jar or archive on Maven Central;
#         ("url", url); ("gh", "owner/repo", regex of the asset) latest release on GitHub.
TOOLS = {
    # A CRaC JDK, for the ceiling only: the panel runs on the stock JDK.
    "zulu-crac": ("ceiling", ("url", "https://cdn.azul.com/zulu/bin/zulu25.36.205-ca-crac-jdk25.0.4.1-linux_x64.tar.gz"), []),
    # Build
    "maven": ("build", ("url", f"{AP}/maven/maven-3/3.9.11/binaries/apache-maven-3.9.11-bin.tar.gz"),
              [("mvn --version", ["mvn", "--version"])]),
    "gradle": ("build", ("url", "https://services.gradle.org/distributions/gradle-9.2.0-bin.zip"),
               [("gradle --version", ["gradle", "--version"])]),
    "ant": ("build", ("url", f"{AP}/ant/binaries/apache-ant-1.10.15-bin.tar.gz"),
            [("ant -version", ["ant", "-version"]), ("ant -f Build.xml", ["ant", "-q", "-f", "Build.xml"])]),
    # JDK
    "jdk": ("jdk", None,
            [("javac --version", ["javac", "--version"]), ("javac Hello.java", ["javac", "-d", "out", "Hello.java"]),
             ("java Hello.java", ["java", "Hello.java"]), ("jar --version", ["jar", "--version"]),
             ("keytool -help", ["keytool", "-help"]), ("jshell --version", ["jshell", "--version"]),
             ("javadoc --version", ["javadoc", "--version"])]),
    # Languages
    "kotlin": ("lang", ("gh", "JetBrains/kotlin", r"^kotlin-compiler-[\d.]+\.zip$"),
               [("kotlinc -version", ["kotlinc", "-version"])]),
    "groovy": ("lang", ("maven", "org/apache/groovy/groovy-binary", "zip"),
               [("groovy --version", ["groovy", "--version"]), ("groovy -e", ["groovy", "-e", "println 1"])]),
    "scala": ("lang", ("gh", "scala/scala3", r"^scala3-[\d.]+\.tar\.gz$"),
              [("scalac -version", ["scalac", "-version"])]),
    "jruby": ("lang", ("maven", "org/jruby/jruby-dist", "bin.tar.gz"),
              [("jruby --version", ["jruby", "--version"]), ("jruby -e", ["jruby", "-e", "puts 1"])]),
    "jython": ("lang", ("maven", "org/python/jython-standalone", "jar"),
               [("jython --version", JAR("jython", "--version")), ("jython -c", JAR("jython", "-c", "print(1)"))]),
    "clojure": ("lang", ("clojure", None),
                [("clojure -e", ["java", "-cp", f"{J}/clojure/*", "clojure.main", "-e", "(+ 1 2)"])]),
    "jbang": ("lang", ("gh", "jbangdev/jbang", r"^jbang-[\d.]+\.zip$"), [("jbang --version", ["jbang", "--version"])]),
    # Ops and data
    "kafka": ("ops", ("url", f"{AP}/kafka/4.1.0/kafka_2.13-4.1.0.tgz"),
              [("kafka-topics --version", ["kafka-topics.sh", "--version"]),
               ("kafka-configs --help", ["kafka-configs.sh", "--help"])]),
    "zookeeper": ("ops", ("url", f"{AP}/zookeeper/zookeeper-3.9.4/apache-zookeeper-3.9.4-bin.tar.gz"),
                  [("zkServer version", ["zkServer.sh", "version"])]),
    "liquibase": ("ops", ("gh", "liquibase/liquibase", r"^liquibase-[\d.]+\.tar\.gz$"),
                  [("liquibase --version", ["liquibase", "--version"])]),
    "trino": ("ops", ("maven", "io/trino/trino-cli", "executable.jar"),
              [("trino --version", JAR("trino", "--version"))]),
    "sonar-scanner": ("ops", ("url", "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-7.3.0.5189.zip"),
                      [("sonar-scanner --version", ["sonar-scanner", "--version"])]),
    "jmeter": ("ops", ("url", f"{AP}/jmeter/binaries/apache-jmeter-5.6.3.tgz"), [("jmeter --version", ["jmeter", "--version"])]),
    # Quality
    "checkstyle": ("quality", ("gh", "checkstyle/checkstyle", r"^checkstyle-[\d.]+-all\.jar$"),
                   [("checkstyle --version", JAR("checkstyle", "--version")),
                    ("checkstyle Hello.java", JAR("checkstyle", "-c", "/sun_checks.xml", "Hello.java"))]),
    "pmd": ("quality", ("gh", "pmd/pmd", r"^pmd-dist-[\d.]+-bin\.zip$"),
            [("pmd --version", ["pmd", "--version"]),
             ("pmd check Hello.java", ["pmd", "check", "--no-progress", "--no-cache", "-R", "rulesets/java/quickstart.xml", "-d", "Hello.java"])]),
    "ktlint": ("quality", ("gh", "pinterest/ktlint", r"^ktlint$"),
               [("ktlint --version", ["ktlint", "--version"]), ("ktlint Sample.kt", ["ktlint", "Sample.kt"])]),
    "google-java-format": ("quality", ("gh", "google/google-java-format", r"^google-java-format-[\d.]+-all-deps\.jar$"),
                           [("google-java-format --version", JAR("google-java-format", "--version"))]),
    "detekt": ("quality", ("gh", "detekt/detekt", r"^detekt-cli-[\d.]+-all\.jar$"),
               [("detekt --version", JAR("detekt", "--version"))]),
    "spotbugs": ("quality", ("gh", "spotbugs/spotbugs", r"^spotbugs-[\d.]+\.tgz$"),
                 [("spotbugs -version", ["spotbugs", "-version"])]),
    "jacoco": ("quality", ("maven", "org/jacoco/org.jacoco.cli", "nodeps.jar"),
               [("jacoco version", JAR("jacoco", "version"))]),
    # Generators and converters
    "openapi-generator": ("gen", ("maven", "org/openapitools/openapi-generator-cli", "jar"),
                          [("openapi-generator version", JAR("openapi-generator", "version")),
                           ("openapi-generator validate", JAR("openapi-generator", "validate", "-i", "spec.yaml"))]),
    "swagger-codegen": ("gen", ("maven", "io/swagger/codegen/v3/swagger-codegen-cli", "jar"),
                        [("swagger-codegen version", JAR("swagger-codegen", "version"))]),
    "plantuml": ("gen", ("gh", "plantuml/plantuml", r"^plantuml-[\d.]+\.jar$"),
                 [("plantuml -version", JAR("plantuml", "-version")),
                  ("plantuml sample.puml", JAR("plantuml", "-tsvg", "sample.puml"))]),
    "apktool": ("gen", ("gh", "iBotPeaches/Apktool", r"^apktool_[\d.]+\.jar$"), [("apktool --version", JAR("apktool", "--version"))]),
    "antlr": ("gen", ("maven", "org/antlr/antlr4", "complete.jar"), [("antlr, usage", JAR("antlr"))]),
    "closure-compiler": ("gen", ("maven", "com/google/javascript/closure-compiler", "jar"),
                         [("closure-compiler --version", JAR("closure-compiler", "--version"))]),
    "bfg": ("gen", ("maven", "com/madgag/bfg", "jar"), [("bfg --version", JAR("bfg", "--version"))]),
    "spring-boot": ("gen", ("maven", "org/springframework/boot/spring-boot-cli", "bin.tar.gz"), [("spring --version", ["spring", "--version"])]),
    "quarkus": ("gen", ("maven", "io/quarkus/quarkus-cli", "runner.jar"), [("quarkus --version", JAR("quarkus", "--version"))]),
}
