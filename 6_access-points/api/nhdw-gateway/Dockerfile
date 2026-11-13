FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Model-graduation validator harness. The source is
# 2_pipelines/ml/registry/validators.py; the build scripts copy it into this
# build context before the image build. app/routes/graduation.py loads it from
# /app/validators.py. Static checks (schema, dependencies, resources) are the
# promotion gate; the optional 'smoke' validator needs a docker CLI, which this
# image does not have, so it skips itself.
COPY validators.py /app/validators.py

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
