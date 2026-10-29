# One Ruby version, from the official image, for the Ruby suite. Python 3
# runs the test harness. The client comes prebuilt (SPEEDRUNNER_BIN).
#   docker build --build-arg VERSION=3.4 -t speedrunner-rb3.4 -f docker/lang/ruby.Dockerfile docker
ARG VERSION
FROM ruby:${VERSION}-slim

ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends python3 procps rsync \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /root/speedrunner

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
