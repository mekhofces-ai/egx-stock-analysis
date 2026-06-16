FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY dashboard ./dashboard
COPY alembic ./alembic
COPY alembic.ini .
COPY data/stocks_sample.csv ./data/stocks_sample.csv
COPY data/ohlcv_sample.csv ./data/ohlcv_sample.csv

RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "-m", "app.main"]
