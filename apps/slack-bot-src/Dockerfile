FROM python:3.13-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY slack_bot.py .

USER 1000
CMD ["python", "-u", "slack_bot.py"]
