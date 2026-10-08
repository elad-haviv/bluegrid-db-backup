FROM python:3.12-alpine

RUN apk add --no-cache curl \
 && pip install --no-cache-dir flask boto3 gunicorn

WORKDIR /app
COPY app.py .

EXPOSE 8000
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
