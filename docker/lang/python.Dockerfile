# One Python version, from the official image, for the Python suites
# (matrix, scripts). The client comes prebuilt (SPEEDRUNNER_BIN).
#   docker build --build-arg VERSION=3.13 -t speedrunner-py3.13 -f docker/lang/python.Dockerfile docker
ARG VERSION
FROM python:${VERSION}-slim

ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends procps rsync zsh \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /root/speedrunner
RUN python3 -m venv venv-aws && venv-aws/bin/pip install -q --no-cache-dir --disable-pip-version-check awscli \
    && python3 -m venv venv-test

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
