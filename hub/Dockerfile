FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hub_app.py .

# Run as a non-root user
RUN useradd -m cardea && mkdir /data && chown cardea /data
USER cardea

ENV HUB_DB=/data/hub.db
EXPOSE 5000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"
# One process (PINs and inbox live in memory), several threads so an idle browser connection cannot block everyone
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "1", "-k", "gthread", "--threads", "8", "hub_app:app"]
