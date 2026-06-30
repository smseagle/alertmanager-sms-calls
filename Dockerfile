FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application package
COPY src/smseagle_adapter ./smseagle_adapter

# Run as a non-root user
RUN useradd -m -u 10001 adapter
USER adapter

EXPOSE 8080

# Healthcheck against the /healthz endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["uvicorn", "smseagle_adapter.app:app", "--host", "0.0.0.0", "--port", "8080"]
