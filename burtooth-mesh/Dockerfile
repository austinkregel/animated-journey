FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY addon/ /app/addon/
COPY frontend/ /app/frontend/

WORKDIR /app

CMD ["python3", "-m", "addon.server"]
