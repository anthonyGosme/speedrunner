# pre-commit, end to end: a small Python project whose commit runs the usual
# hooks (black, isort, flake8, mypy), natively and through speedrunner. See
# compare.py. speedrunner is the published release, installed as users do.
FROM python:3.11-slim-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# The tools twice: in pre-commit's own environments (language: python, the
# default) and on PATH, for hooks declared language: system.
RUN pip install -q --disable-pip-version-check pre-commit==4.7.0 black==26.10.1 isort==9.0.2 flake8==7.5.0 mypy==2.4.1
ARG SPEEDRUNNER_VERSION=v0.3.1
RUN curl -fsSL "https://github.com/anthonyGosme/speedrunner/releases/download/$SPEEDRUNNER_VERSION/install.sh" \
    | SPEEDRUNNER_VERSION=$SPEEDRUNNER_VERSION sh
COPY precommit/project /opt/project
COPY precommit/compare.py /opt/bench/compare.py
COPY panel.py /opt/bench/panel.py
# pre-commit's environments, built once (network), for the default config.
RUN cd /opt/project && git init -q && git add -A && git -c user.email=a@b -c user.name=b commit -qm init \
    && cp .pre-commit-config.python.yaml .pre-commit-config.yaml && pre-commit install-hooks
