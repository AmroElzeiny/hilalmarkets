FROM node:22.14-alpine AS landing-build

WORKDIR /landing
COPY Hilal-Markets-Website/package.json Hilal-Markets-Website/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY Hilal-Markets-Website ./
# vite.config.ts aliases `motion` to this file with a `../` path so it reads the
# dashboard's own vendored copy instead of shipping a second one — see
# Hilal-Markets-Website/vite.config.ts. That path resolves outside the Docker build
# context for this stage unless the same relative location is copied in here.
COPY src/ai_market_monitor/static/vendor /src/ai_market_monitor/static/vendor
RUN pnpm typecheck && pnpm build

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /app

# This step used to be `pip install --upgrade pip setuptools wheel`, with no versions.
# Every build therefore took whatever those three projects had published that minute, so
# a bad release upstream could break production and CI without a single change in this
# repository. The installer is pinned instead. setuptools and wheel are not installed
# globally at all: this project builds with hatchling, and pip creates an isolated build
# environment per package from that package's own `build-system.requires`.
ARG PIP_VERSION=26.2.1
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install "pip==${PIP_VERSION}"

# Dependencies are installed from the manifest alone, before any application code is
# copied in. `COPY src ./src` used to sit above the install, which put every Python file
# in the cache key: changing one line of code re-downloaded and re-installed all ninety
# packages, and gave every rebuild a fresh chance to fail on a slow index or a dropped
# connection. Now this layer is rebuilt only when pyproject.toml itself changes.
COPY pyproject.toml ./
RUN python -c "import tomllib, pathlib; manifest = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); pathlib.Path('requirements.txt').write_text(chr(10).join(manifest['project']['dependencies']))"
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --prefer-binary -r requirements.txt

COPY README.md ./
COPY src ./src
COPY --from=landing-build /landing/dist ./src/ai_market_monitor/static/landing
COPY scripts ./scripts
COPY Notion ./Notion
COPY HilalMarkets_Sharia_Methodology_Import_Pack ./HilalMarkets_Sharia_Methodology_Import_Pack
COPY alembic.ini ./
COPY alembic ./alembic
# `--no-deps` is safe here and not a shortcut: the layer above installed exactly
# `project.dependencies`, and any edit to that list changes pyproject.toml, which
# rebuilds that layer before this one runs.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps --prefer-binary .

FROM base AS test

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --prefer-binary ".[dev]"

FROM base AS runtime

# Chromium, for the Shariah source pipeline. `services/sharia_page_render.py` reads a
# project's blog or forum when the page only exists after its JavaScript has run, which is
# now most of them. Without this the setting that turns rendering on has nothing to turn
# on, and the resolver reports a live page as unreadable.
#
# `--with-deps` installs the system libraries Chromium needs on Debian slim. It is one
# command rather than a hand-written apt list on purpose: the list belongs to Playwright's
# own release and changes with it.
#
# `PLAYWRIGHT_BROWSERS_PATH` puts the browser somewhere every user can read. The default
# is `/root/.cache/ms-playwright`, which a container that drops to a non-root user cannot
# open — and the failure is silent, arriving as "the browser would not start".
#
# The image grows by roughly 400 MB. Disk is not what is short on this server; memory is,
# and what a running browser may spend is bounded in `sharia_page_render.py` — one browser
# per sweep, one page at a time, no images, and a hard page budget.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# The same way docker-compose.prod.yml starts it: several workers, each retired after a
# bounded number of requests. This is the image's own default, so running it without a
# compose file cannot quietly give a single process — which is the shape that went down
# twice on 22 August 2026. The numbers live in core/config.py; see serve.py.
#
# No migration here on purpose. Compose runs `alembic upgrade head` once before this, and
# a migration in the image default would run again for every container started from it.
CMD ["python", "-m", "ai_market_monitor.serve"]
