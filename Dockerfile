FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli docker-compose curl ripgrep \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 25086

CMD ["python", "bot.py"]
