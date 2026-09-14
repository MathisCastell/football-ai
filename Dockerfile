FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["gunicorn", "main:app", "--workers", "1", "--threads", "8", "--timeout", "120", "--bind", "0.0.0.0:5000"]
