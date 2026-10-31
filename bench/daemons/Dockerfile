# speedrunner against the daemons that tools ship for their own slow start
# (eslint_d, prettierd, rubocop --server, dmypy, blackd): see compare.py.
# speedrunner is the published release, installed as users install it.
FROM node:24-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv ruby ruby-dev curl \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g --no-fund --no-audit eslint@9 eslint_d@14 prettier @fsouza/prettierd && npm cache clean --force
RUN python3 -m venv /opt/py && /opt/py/bin/pip install -q --disable-pip-version-check mypy "black[d]"
RUN gem install -q --no-document rubocop
ARG SPEEDRUNNER_VERSION=v0.3.1
RUN curl -fsSL "https://github.com/anthonyGosme/speedrunner/releases/download/$SPEEDRUNNER_VERSION/install.sh" \
    | SPEEDRUNNER_VERSION=$SPEEDRUNNER_VERSION sh
ENV PATH=/opt/py/bin:$PATH NO_UPDATE_NOTIFIER=1 npm_config_update_notifier=false
COPY daemons/compare.py /opt/bench/compare.py
COPY panel.py /opt/bench/panel.py
