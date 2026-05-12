FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy main.py from app/
COPY app/main.py .

# Copy New_Model as a package — keeps the New_Model/ folder structure intact
COPY New_Model/ ./New_Model/

ENV MAX_DEPTH=5
ENV TOP_K=3

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]