FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config.py main.py ./
COPY alembic.ini ./
COPY db/ db/
COPY migrations/ migrations/
COPY handlers/ handlers/
COPY middlewares/ middlewares/
COPY keyboards/ keyboards/
COPY services/ services/
CMD ["python", "main.py"]
