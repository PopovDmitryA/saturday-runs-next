FROM python:3.12-slim

WORKDIR /app

# Только рантайм. psycopg[binary] и pymssql приезжают колёсами со своими
# libpq и FreeTDS внутри, Pillow и pillow-heif — тоже; заголовки компиляции
# (libpq-dev, freetds-dev) образу не нужны. До 21.09.2026 сюда же ставились
# pytest, ruff, mypy и fakeredis (extras [dev]) — на проде им делать нечего,
# CI ставит их себе сам (.github/workflows/ci.yml).
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
