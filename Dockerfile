FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY reports ./reports

RUN uv pip install --system -e .

EXPOSE 8080
CMD ["sh", "-c", "uvicorn --proxy-headers --forwarded-allow-ips='*' --factory ci_engine.ui.app:create_app --host 0.0.0.0 --port ${PORT}"]
