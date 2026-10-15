FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# One worker: sessions live in memory (see wsgi.py)
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", "--bind", "0.0.0.0:8000", "wsgi:application"]
