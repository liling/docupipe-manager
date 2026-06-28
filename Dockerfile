# DocuPipe Manager
# Build context is the parent lab/ directory (set in docker-compose.yml),
# so both docupipe-manager/ and xinyi-platform/ sources are visible.

ARG DWS_VERSION=1.0.43

# Stage: fetch dws (DingTalk workspace CLI) for the target arch
FROM python:3.12-slim AS dws
ARG TARGETARCH=amd64
ARG DWS_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /tmp/dws.tar.gz \
       "https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download/v${DWS_VERSION}/dws-linux-${TARGETARCH}.tar.gz" \
    && tar -xzf /tmp/dws.tar.gz -C /usr/local/bin dws \
    && chmod +x /usr/local/bin/dws \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copy xinyi-platform first (DM depends on it via pyproject.toml).
# Only copy runtime source — tests, .git, docs are excluded.
COPY xinyi-platform/pyproject.toml xinyi-platform/uv.lock /xinyi-platform/
COPY xinyi-platform/xinyi_platform /xinyi-platform/xinyi_platform/

# Copy DM manifests and sync deps.
COPY docupipe-manager/pyproject.toml docupipe-manager/uv.lock ./
RUN uv sync --frozen

COPY docupipe-manager/docupipe_manager ./docupipe_manager
RUN uv pip install -e .


FROM python:3.12-slim

WORKDIR /app

RUN useradd -m -s /bin/bash docupipe

COPY --from=builder /app /app
COPY --from=builder /xinyi-platform /xinyi-platform
COPY --from=dws /usr/local/bin/dws /usr/local/bin/dws

USER docupipe

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

EXPOSE 8002

CMD ["uvicorn", "docupipe_manager.main:app", "--host", "0.0.0.0", "--port", "8002"]
