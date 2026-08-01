FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app/src

EXPOSE 9000

CMD ["uvicorn", "blockhost_agent.agent:app", "--host", "0.0.0.0", "--port", "9000"]