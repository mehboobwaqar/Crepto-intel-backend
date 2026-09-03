FROM python:3.11-slim

WORKDIR /app

# Ensure logs appear immediately
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Hugging Face default port is 7860
ENV PORT=7860

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy codebase and database
COPY . .

# Ensure SQLite database is writable by Hugging Face non-root user (UID 1000)
RUN chmod -R 777 /app

# Expose port
EXPOSE 7860

# Start application (REST API on port 7860 + Background Binance Streamer)
CMD ["python3", "main.py", "serve"]
