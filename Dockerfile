# Gleaner — one image containing the agent, the control plane and the console.
#
# Stage one builds the console; stage two runs the API and serves the built assets
# from it, so there is a single artifact to deploy and a single origin in the
# browser. No reverse proxy, no CORS, no second service to keep in step.

# ---------- stage 1: build the console ----------
FROM node:20-slim AS console
WORKDIR /console

# Install against the lockfile first so an unchanged dependency set stays cached
# across source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------- stage 2: the runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GLEANER_AWS_REGION=us-east-1 \
    GLEANER_CONSOLE_DIR=/app/frontend/dist

WORKDIR /app

# Dependencies before source, again for layer caching.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# GLEANER_CONSOLE_DIR points here, because the relative fallback in app.py only
# holds for a source checkout and this package is pip-installed.
COPY --from=console /console/dist /app/frontend/dist

# Fail the build now, loudly, rather than shipping an image that serves no console.
RUN python -c "from gleaner.api.app import console_dir; d=console_dir(); assert d, 'console not found'; print('console served from', d)"

EXPOSE 8080
# App Runner sets PORT; default to 8080 for a plain `docker run`.
CMD ["sh", "-c", "uvicorn gleaner.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
