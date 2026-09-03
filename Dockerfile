FROM python:3.11-slim

WORKDIR /app

# Ensure logs appear immediately
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy codebase and database
COPY . .

# Expose port
EXPOSE 8000

# Start the application (REST API + Background Binance WebSocket Streamer)
CMD ["python3", "main.py", "serve"]
