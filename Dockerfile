# =============================================================================
# OpenFMR Admin UI - Dockerfile
# =============================================================================
# Lightweight Python 3.11 image running the FastAPI application via Uvicorn.
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose the application port
EXPOSE 8000

# Run Uvicorn pointing at the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
