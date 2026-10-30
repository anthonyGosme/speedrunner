# Node go/no-go (J0): well-known Node CLIs, a C compiler for the fork addon,
# Python for the harness (the image already has yarn). Build and run: see ceiling.py.
FROM node:24-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
RUN npm install -g --no-fund --no-audit eslint@9 typescript prettier jest vite webpack webpack-cli \
      @angular/cli next react react-dom @nestjs/cli firebase-tools vercel pnpm \
    && npm cache clean --force
ENV NG_CLI_ANALYTICS=false NEXT_TELEMETRY_DISABLED=1 FIREBASE_CLI_EXPERIMENTS= \
    VERCEL_TELEMETRY_DISABLED=1 NO_UPDATE_NOTIFIER=1 npm_config_update_notifier=false
COPY fork.c /opt/j0/fork.c
RUN gcc -O2 -shared -fPIC -I/usr/local/include/node -o /opt/j0/fork.node /opt/j0/fork.c
