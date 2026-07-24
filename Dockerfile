# Self-contained image. Built where there is internet; needs none at runtime
# (ci-doctor only ever talks to your configured GitLab and LLM endpoints).
FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# No network at job runtime, no telemetry, no update checks.
ENTRYPOINT ["ci-doctor"]
