FROM python:3.11-slim

# curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# graphiti-core 0.3.x requires python-dotenv as a transitive dependency
# (graphiti_core/graphiti.py imports dotenv at module level)
RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    uvicorn==0.30.6 \
    graphiti-core==0.3.0 \
    neo4j==5.26.0 \
    httpx==0.27.0 \
    python-dotenv==1.0.1

COPY service.py .
COPY bootstrap.py .

EXPOSE 8002

CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8002"]
