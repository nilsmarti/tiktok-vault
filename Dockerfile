FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
USER app
ENV PYTHONUNBUFFERED=1 PATH="/home/app/.local/bin:${PATH}"
