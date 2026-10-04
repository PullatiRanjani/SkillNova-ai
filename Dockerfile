FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000 \
    SKILLNOVA_DB_PATH=/var/data/skillnova.db

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ default-jdk-headless nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./

EXPOSE 10000
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --timeout 120 --access-logfile - --error-logfile - wsgi:app"]
