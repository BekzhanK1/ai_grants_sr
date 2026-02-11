# builder: install dependencies only
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# runtime: minimal image without pip/cache
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /deps /usr/local/lib/python3.12/site-packages
COPY --from=builder /deps/bin /usr/local/bin
COPY app ./app

EXPOSE 8555

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8555"]
