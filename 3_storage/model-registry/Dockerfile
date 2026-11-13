FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        mlflow==2.20.1 \
        psycopg2-binary==2.9.10 \
        boto3==1.35.99

EXPOSE 5000

# Default command runs the tracking server; override it to run the mlflow CLI
# from the same image.
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
