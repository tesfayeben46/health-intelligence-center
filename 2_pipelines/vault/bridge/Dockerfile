FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# clickhouse-driver: native TCP, used because CH HTTP doesn't stream blocks
# back fast enough for multi-GB syncs. psycopg2 for bulk COPY into PG.
RUN pip install --no-cache-dir \
        clickhouse-driver==0.2.10 \
        psycopg2-binary==2.9.10

WORKDIR /bridge
COPY sync.py lineage.py /bridge/

ENTRYPOINT ["python", "/bridge/sync.py"]
