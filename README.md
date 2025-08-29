# Клонируй репозиторий
`git clone https://github.com/tochiv/csBot.git`<br>
`cd csBot`

# Создай .env файл
`cp .env.example .env`

# Заполни переменные
`nano .env`

# Обязательные переменные
`BOT_TOKEN=your_telegram_bot_token_here`<br>
`POSTGRES_HOST=postgres`<br>
`POSTGRES_DB=telegram_bot`<br>
`POSTGRES_USER=bot_user`<br>
`POSTGRES_PASSWORD=strong_password_here`<br>
`POSTGRES_PORT=5432`<br>

# Запусти контейнеры
`docker-compose up -d`

# Проверь логи
`docker-compose logs -f bot`

# Для редактирования
`docker compose down`<br>
После того, как внёс изменения<br>
`docker compose build`<br>
`docker compose up`<br>
