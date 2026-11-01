# Fedora's Node, built without the code cache of its internals (as
# Homebrew's node@22): the daemon starts it without --no-lazy. For the Node
# suite; the client comes prebuilt (SPEEDRUNNER_BIN).
#   docker build -t speedrunner-node-fedora -f docker/lang/node-fedora.Dockerfile docker
FROM fedora:43

ENV LANG=C.UTF-8
RUN dnf install -y -q nodejs python3 procps-ng rsync util-linux-script && dnf clean all
WORKDIR /root/speedrunner

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
