FROM python:3.11-slim

WORKDIR /app

# Install only runtime libs (no compiler — all deps have Linux wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 10000

CMD ["python", "run.py"]
