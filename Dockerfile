FROM python:3.11-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment (.venv)
RUN uv sync --frozen --no-dev

# Copy the backend source code
COPY backend ./backend
COPY main.py ./

# Provide a default PORT if not set by the cloud provider
ENV PORT=7860
EXPOSE $PORT

# Start the application using uv run
CMD ["sh", "-c", "cd backend && uv run uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
