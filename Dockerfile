# Официальный легкий образ Python
FROM python:3.12-slim

# Чтобы питон не буферизировал вывод
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Настраиваем рабочую директорию внутри контейнера
WORKDIR /app

# Устанавливаем зависимости системы (для psycopg2 и т.п.)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходники Django-проекта
COPY web/ /app/web/

# Если manage.py лежит прямо в web/, а не в корне
WORKDIR /app/web

# Открываем порт
EXPOSE 8000

# Команда по умолчанию:
# 1) миграции
# 2) сборка статики
# 3) запуск сервера
CMD sh -c "python manage.py migrate && \
           python manage.py collectstatic --noinput && \
           python manage.py runserver 0.0.0.0:8000"
