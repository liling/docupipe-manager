FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

COPY docupipe_manager ./docupipe_manager

COPY alembic.ini ./

CMD alembic upgrade head && uvicorn docupipe_manager.main:app --host 0.0.0.0 --port 8002
