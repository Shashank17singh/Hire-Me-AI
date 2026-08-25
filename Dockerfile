FROM python:3.11-slim

WORKDIR /app

# Copy the entire application
COPY . ./

# Install dependencies using standard pip
RUN pip install --no-cache-dir .

# Provide a default PORT
ENV PORT=7860
EXPOSE $PORT

# Start the application natively
CMD ["sh", "-c", "cd backend && uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
