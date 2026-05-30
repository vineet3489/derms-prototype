FROM python:3.11-slim

WORKDIR /app

# System deps for pandapower (numpy/scipy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data directory for SQLite persistence (Fly volume mounts here)
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "run.py"]
