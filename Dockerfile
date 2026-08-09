FROM python:3.11-slim

COPY ci_doctor /src/ci_doctor
COPY pyproject.toml README.md LICENSE /src/
RUN pip install --no-cache-dir /src && rm -rf /src

WORKDIR /app

ENTRYPOINT ["ci-doctor"]
