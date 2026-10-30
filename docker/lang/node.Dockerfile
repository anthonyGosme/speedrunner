# One Node version, from the official image (Debian, or Alpine for the musl
# build of the addon), for the Node suite. Python 3 runs the test harness;
# script(1) gives it a terminal. The client comes prebuilt (SPEEDRUNNER_BIN).
#   docker build --build-arg VERSION=24 -t speedrunner-node24 -f docker/lang/node.Dockerfile docker
#   docker build --build-arg VERSION=24-alpine ...
ARG VERSION
FROM node:${VERSION}

ENV LANG=C.UTF-8
RUN if command -v apk > /dev/null; then apk add --no-cache python3 bash procps rsync util-linux-misc; \
    else apt-get update && apt-get install -y --no-install-recommends python3 procps rsync bsdutils \
         && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /root/speedrunner

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
