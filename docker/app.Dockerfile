# Task-runner container: migrations, pipelines, CLI.
# Python 3.12 - the system's 3.14 is ahead of the ML ecosystem.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=2.1.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev postgresql-client \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# The container runs as the host user (arbitrary UID with no passwd entry, so
# HOME resolves to /). Give ZenML and anything else HOME-seeking a writable
# place. World-writable because the UID is not known at build time; a named
# volume mounted here inherits these permissions on first use.
RUN mkdir -p /state/zenml /state/home \
 && chmod -R 777 /state
ENV HOME=/state/home \
    ZENML_CONFIG_PATH=/state/zenml \
    ZENML_ANALYTICS_OPT_IN=false

WORKDIR /app

# Dependency layer, cached independently of source changes.
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --with dev

COPY . .
RUN poetry install --only-root

CMD ["sleep", "infinity"]
