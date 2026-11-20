FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
COPY packages/wheels/ /wheels/
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--threads", "4", "--worker-class", "gthread", "--timeout", "120", "app:app"]
