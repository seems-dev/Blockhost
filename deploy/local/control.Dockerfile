FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app/src

EXPOSE 8000

# Run migrations first, then start the server
CMD sh -c "alembic upgrade head && uvicorn blockhost_backend.main:app --host 0.0.0.0 --port 8000"
