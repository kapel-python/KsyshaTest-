FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli docker-compose curl ripgrep git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Bake the git commit hash into the image so the app can read it at runtime.
# Pass with: docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) ...
# Falls back to "unknown" when building without git context.
ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/.git_commit

EXPOSE 25086

CMD ["python", "bot.py"]
