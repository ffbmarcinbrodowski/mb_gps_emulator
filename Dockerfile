FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EMULATOR_DB=/data/emulator.db \
    EMULATOR_PORT=5057

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data

EXPOSE 5057

# Exactly ONE worker: the emulator threads and their state live inside the
# process, so a second worker would run a second, independent emulator.
# Threads handle concurrent API requests instead.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--bind", "0.0.0.0:5057", \
     "--access-logfile", "-", "app:app"]
