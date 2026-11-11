FROM python:3.12.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 scopepilot \
    && mkdir -p /app/data \
    && chown -R scopepilot:scopepilot /app

USER scopepilot

EXPOSE 8000

CMD ["uvicorn", "scopepilot.main:app", "--host", "0.0.0.0", "--port", "8000"]

