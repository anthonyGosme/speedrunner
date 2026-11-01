# Dev and bench environment for speedrunner: Debian 12, with the tools laid
# out as on the dev VPS (docs/ARCHITECTURE.md §10), so that tests and benches
# run unchanged.
# The working copy is copied in at run time (docker/run.sh).
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev ruby ruby-dev build-essential \
        php8.2-cli php8.2-xml php8.2-mbstring php8.2-curl php8.2-zip php8.2-sqlite3 \
        composer phpunit php-codesniffer \
        cargo rustc zsh hyperfine procps rsync git ca-certificates unzip curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /root/speedrunner
RUN python3 -m venv venv-aws && venv-aws/bin/pip install -q --no-cache-dir awscli \
    && python3 -m venv venv-panel && venv-panel/bin/pip install -q --no-cache-dir \
        ansible black poetry httpie pre-commit mypy pytest flake8 yamllint \
    && python3 -m venv venv-test \
    && python3 -m venv venv-moto && venv-moto/bin/pip install -q --no-cache-dir "moto[server]" \
    && python3 -m venv venv-rec && venv-rec/bin/pip install -q --no-cache-dir asciinema
RUN GEM_HOME=/root/speedrunner/gems-rb gem install --no-document rubocop rake bundler rspec
RUN mkdir php && cd php && COMPOSER_ALLOW_SUPERUSER=1 \
        composer create-project --no-interaction --quiet laravel/laravel laravel

COPY run.sh /usr/local/bin/speedrunner-dev
ENTRYPOINT ["speedrunner-dev"]
