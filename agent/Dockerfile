FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_app.py .
COPY templates templates

# Run as a non-root user
RUN useradd -m cardea && mkdir /data && chown cardea /data
USER cardea

ENV VAULT_FILE=/data/vault.json
ENV PORT=5001
EXPOSE 5001
HEALTHCHECK CMD python -c "import os, urllib.request as u; u.urlopen('http://localhost:' + os.environ['PORT'] + '/health')"
CMD gunicorn -b 0.0.0.0:$PORT -w 1 -k gthread --threads 8 agent_app:app
