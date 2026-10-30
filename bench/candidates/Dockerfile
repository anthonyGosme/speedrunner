# Candidate tools for the panel (bench/candidates.py), each Python tool in a
# venv of its own and each PHP tool in a Composer project of its own, so that
# their dependencies do not clash. A tool that fails to install is left out:
# the panel reports it as not installed.
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 COMPOSER_ALLOW_SUPERUSER=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev ruby ruby-dev build-essential pkg-config \
        php8.2-cli php8.2-xml php8.2-mbstring php8.2-curl php8.2-zip php8.2-sqlite3 \
        php8.2-gd php8.2-intl php8.2-mysql composer \
        lsb-release command-not-found libpq5 libyaml-dev procps git unzip ca-certificates curl \
    && apt-get update && (update-command-not-found || true) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /root/speedrunner/cand
RUN set -u; for spec in \
        dbt:dbt-core,dbt-duckdb airflow:apache-airflow checkov:checkov cfn-lint:cfn-lint \
        sam:aws-sam-cli openstack:python-openstackclient oci:oci-cli pylint:pylint \
        sqlfluff:sqlfluff pgcli:pgcli mycli:mycli litecli:litecli yt-dlp:yt-dlp meson:meson \
        conan:conan pio:platformio mkdocs:mkdocs pipenv:pipenv pdm:pdm hatch:hatch twine:twine \
        bandit:bandit codespell:codespell cookiecutter:cookiecutter copier:copier nox:nox; do \
      name=${spec%%:*}; pkgs=$(echo "${spec#*:}" | tr , ' '); \
      python3 -m venv "$name" && "$name/bin/pip" install -q --no-cache-dir $pkgs \
        || { echo "FAILED: $name"; rm -rf "$name"; }; \
    done

RUN GEM_HOME=/root/speedrunner/gems-rb gem install --no-document \
        fastlane kamal capistrano brakeman standard asciidoctor puppet reek jekyll \
    || echo "FAILED: some gems"

WORKDIR /root/speedrunner/php
RUN for spec in pint:laravel/pint infection:infection/infection phpmd:phpmd/phpmd \
        dep:deployer/deployer behat:behat/behat pest:pestphp/pest wp:wp-cli/wp-cli-bundle; do \
      name=${spec%%:*}; mkdir "c-$name" && cd "c-$name" && echo '{}' > composer.json \
      && composer config --no-interaction allow-plugins true \
      && composer require --no-interaction --quiet "${spec#*:}" \
      || echo "FAILED: $name"; cd /root/speedrunner/php; \
    done \
    && (composer create-project --no-interaction --quiet symfony/skeleton symfony || echo "FAILED: symfony") \
    && (composer create-project --no-interaction --quiet --no-install drupal/recommended-project:^10 drupal \
        && cd drupal && composer config --no-interaction allow-plugins true \
        && composer require --no-interaction --quiet "drush/drush:^12 || ^13" || echo "FAILED: drupal")
WORKDIR /root/speedrunner
