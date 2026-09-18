# One image, three processes: learner app (ari.main), back-office (ari.backoffice), worker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend/src
WORKDIR /app

COPY backend/requirements.lock backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock

COPY alembic.ini ./
COPY backend ./backend
COPY web ./web
COPY backoffice-web ./backoffice-web
COPY cases ./cases

RUN useradd --create-home --uid 10001 ari && mkdir -p /data/content && chown -R ari /data
USER ari

EXPOSE 8000 8100
CMD ["python", "-m", "uvicorn", "ari.main:app", "--host", "0.0.0.0", "--port", "8000"]
