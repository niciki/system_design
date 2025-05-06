FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .

COPY . .

RUN pip install -r requirements.txt

CMD ["uvicorn", "analytics_service.main:app", "--host", "0.0.0.0", "--port", "8002"]

EXPOSE 8000