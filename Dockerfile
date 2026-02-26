FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir httpx fastapi uvicorn

# Copy application files
COPY akash_core.py deploy.py agent.py .

# Expose port 8080
EXPOSE 8080

# Run the FastAPI app
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
