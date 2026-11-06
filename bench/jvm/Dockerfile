# Go/no-go JVM (J0): a stock JDK, nothing of speedrunner. See ANALYSE.md in bench/results.
FROM eclipse-temurin:25-jdk-noble
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3 unzip procps >/dev/null && rm -rf /var/lib/apt/lists
