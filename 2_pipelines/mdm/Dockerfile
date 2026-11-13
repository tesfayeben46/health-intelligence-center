# MDM matcher: one-shot candidate generation (vault -> ops_governance queue).
# Run-to-completion Job, same lifecycle as the dbt stages; it only writes the
# steward candidate queue. Conformed keys move exclusively via decisions.
FROM python:3.12-slim

RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"

WORKDIR /mdm
COPY matcher/matcher.py .

ENTRYPOINT ["python3", "/mdm/matcher.py"]
