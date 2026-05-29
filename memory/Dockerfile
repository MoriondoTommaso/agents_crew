FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    uvicorn==0.30.6 \
    graphiti-core==0.3.0 \
    neo4j==5.26.0 \
    openai==1.35.0 \
    httpx==0.27.0

COPY service.py .
COPY bootstrap.py .

EXPOSE 8002

CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8002"]
