FROM python:3.11-slim

WORKDIR /app

COPY ci-steps/feishu-io/wheels /tmp/wheels
COPY core /app/core
COPY scripts /app/scripts

RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels requests pyyaml && rm -rf /tmp/wheels

ENV PYTHONPATH=/app
