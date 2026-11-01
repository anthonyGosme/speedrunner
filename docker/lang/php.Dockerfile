# One PHP version, from the official image, with the extensions the daemon
# needs (pcntl, posix, FFI, OPcache: built in from 8.5), for the PHP suite. Python 3 runs the
# test harness. The client comes prebuilt (SPEEDRUNNER_BIN).
#   docker build --build-arg VERSION=8.4 -t speedrunner-php8.4 -f docker/lang/php.Dockerfile docker
ARG VERSION
FROM php:${VERSION}-cli

ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends python3 procps rsync libffi-dev \
    && docker-php-ext-configure ffi --with-ffi \
    && exts="pcntl ffi" && { php -m | grep -q 'Zend OPcache' || exts="$exts opcache"; } \
    && docker-php-ext-install -j"$(nproc)" $exts \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /root/speedrunner

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
