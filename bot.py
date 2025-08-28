import json
import itertools
import asyncio
import time
from statistics import mean
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatAction
from aiogram.client.default import DefaultBotProperties
import psycopg2
from psycopg2 import sql
import os
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")


# === Подключение к PostgreSQL ===
def get_db_connection_with_retry(max_retries=10, delay=3):
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=os.getenv('POSTGRES_HOST'),
                database=os.getenv('POSTGRES_DB'),
                user=os.getenv('POSTGRES_USER'),
                password=os.getenv('POSTGRES_PASSWORD'),
                port=os.getenv('POSTGRES_PORT'),
                connect_timeout=5
            )
            print(f"✅ Successfully connected to PostgreSQL (attempt {attempt + 1})")

            # Проверяем что таблицы доступны
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()

            return conn
        except psycopg2.OperationalError as e:
            print(f"⚠️ Connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                print(f"⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("❌ All connection attempts failed")
                raise
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            raise


def get_db_connection():
    return get_db_connection_with_retry()


# === Инициализация базы данных ===
def init_database():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Таблица игроков (основная информация)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                username VARCHAR(255),
                first_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица статистики игроков (отдельная для истории)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_stats (
                id SERIAL PRIMARY KEY,
                player_id INTEGER REFERENCES players(id),
                match_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                adr FLOAT NOT NULL CHECK (adr >= 0 AND adr <= 150),
                kills INTEGER DEFAULT 0,
                deaths INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                rating FLOAT DEFAULT 0.0,
                map VARCHAR(100),
                team VARCHAR(50)
            )
        """)

        # Таблица матчей (игровых сессий)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                is_active BOOLEAN DEFAULT FALSE,
                pinned_message_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                balanced_at TIMESTAMP,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                map VARCHAR(100)
            )
        """)

        # Таблица участников матча
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS match_players (
                id SERIAL PRIMARY KEY,
                match_id INTEGER REFERENCES matches(id),
                player_id INTEGER REFERENCES players(id),
                team INTEGER CHECK (team IN (1, 2)),
                is_confirmed BOOLEAN DEFAULT FALSE,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, player_id)
            )
        """)

        # Таблица балансировки команд
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team_balance (
                id SERIAL PRIMARY KEY,
                match_id INTEGER REFERENCES matches(id),
                team1_players INTEGER[],
                team2_players INTEGER[],
                team1_avg_adr FLOAT,
                team2_avg_adr FLOAT,
                difference FLOAT,
                balanced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица кулдаунов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                id SERIAL PRIMARY KEY,
                player_id INTEGER REFERENCES players(id),
                cooldown_end TIMESTAMP NOT NULL,
                reason VARCHAR(100)
            )
        """)

        conn.commit()
        cursor.close()
        conn.close()
        print("Database initialized successfully")

    except Exception as e:
        print(f"Database initialization error: {e}")


# === Работа с игроками ===
def get_or_create_player(user_id, username, first_name):
    """Получает или создает игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO players (telegram_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) 
            DO UPDATE SET 
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """, (user_id, username, first_name))

        player_id = cursor.fetchone()[0]
        conn.commit()
        return player_id

    except Exception as e:
        print(f"Error getting/creating player: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def add_player_stats(player_id, adr, kills=0, deaths=0, assists=0, map_name=None, team=None):
    """Добавляет статистику для игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Рассчитываем рейтинг (простая формула, можно изменить)
        rating = (adr / 100) + ((kills * 0.3) / (deaths * 0.2)) + (assists * 0.1)

        cursor.execute("""
            INSERT INTO player_stats (player_id, adr, kills, deaths, assists, rating, map, team)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (player_id, adr, kills, deaths, assists, round(rating, 1), map_name, team))

        conn.commit()
        return True

    except Exception as e:
        print(f"Error adding player stats: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def get_player_stats(player_id, limit=10):
    """Получает статистику игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT match_date, adr, kills, deaths, assists, rating, map, team
            FROM player_stats 
            WHERE player_id = %s 
            ORDER BY match_date DESC 
            LIMIT %s
        """, (player_id, limit))

        stats = cursor.fetchall()
        return stats

    except Exception as e:
        print(f"Error getting player stats: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_player_average_stats(player_id):
    """Получает среднюю статистику игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                COUNT(*) as matches,
                ROUND(AVG(adr)) as avg_adr,
                ROUND(AVG(kills)) as avg_kills,
                ROUND(AVG(deaths)) as avg_deaths,
                ROUND(AVG(assists)) as avg_assists,
                ROUND(AVG(rating)) as avg_rating
            FROM player_stats 
            WHERE player_id = %s
        """, (player_id,))

        stats = cursor.fetchone()
        return stats

    except Exception as e:
        print(f"Error getting average stats: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_all_players():
    """Получает всех игроков"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT telegram_id, username, first_name 
            FROM players 
            ORDER BY first_name
        """)

        players = cursor.fetchall()
        return players

    except Exception as e:
        print(f"Error getting players: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


# === Работа с матчами ===
def create_match():
    """Создает новую игровую сессию"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO matches (is_active)
            VALUES (TRUE)
            RETURNING id
        """)

        match_id = cursor.fetchone()[0]
        conn.commit()
        return match_id

    except Exception as e:
        print(f"Error creating match: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_active_match():
    """Получает активный матч"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, pinned_message_id 
            FROM matches 
            WHERE is_active = TRUE 
            ORDER BY created_at DESC 
            LIMIT 1
        """)

        match_data = cursor.fetchone()
        return match_data

    except Exception as e:
        print(f"Error getting active match: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def deactivate_match(match_id):
    """Деактивирует матч"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE matches 
            SET is_active = FALSE 
            WHERE id = %s
        """, (match_id,))

        conn.commit()
        return True

    except Exception as e:
        print(f"Error deactivating match: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def add_player_to_match(match_id, player_id):
    """Добавляет игрока в матч"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO match_players (match_id, player_id)
            VALUES (%s, %s)
            ON CONFLICT (match_id, player_id) DO NOTHING
        """, (match_id, player_id))

        conn.commit()
        return True

    except Exception as e:
        print(f"Error adding player to match: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def remove_player_from_match(match_id, player_id):
    """Удаляет игрока из матча"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM match_players 
            WHERE match_id = %s AND player_id = %s
        """, (match_id, player_id))

        conn.commit()
        return cursor.rowcount > 0

    except Exception as e:
        print(f"Error removing player from match: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def get_match_players(match_id):
    """Получает игроков в матче"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.id, p.telegram_id, p.first_name, p.username
            FROM match_players mp
            JOIN players p ON mp.player_id = p.id
            WHERE mp.match_id = %s
        """, (match_id,))

        players = cursor.fetchall()
        return players

    except Exception as e:
        print(f"Error getting match players: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_match_player_count(match_id):
    """Получает количество игроков в матче"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) 
            FROM match_players 
            WHERE match_id = %s
        """, (match_id,))

        count = cursor.fetchone()[0]
        return count

    except Exception as e:
        print(f"Error getting match player count: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()


def set_match_pinned_message(match_id, message_id):
    """Устанавливает ID закрепленного сообщения для матча"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE matches 
            SET pinned_message_id = %s 
            WHERE id = %s
        """, (message_id, match_id))

        conn.commit()
        return True

    except Exception as e:
        print(f"Error setting pinned message: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


# === Работа с кулдаунами ===
def set_cooldown(player_id, duration_seconds=60, reason="join_cooldown"):
    """Устанавливает кулдаун для игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cooldown_end = time.time() + duration_seconds

        cursor.execute("""
            INSERT INTO cooldowns (player_id, cooldown_end, reason)
            VALUES (%s, to_timestamp(%s), %s)
            ON CONFLICT (player_id) 
            DO UPDATE SET 
                cooldown_end = EXCLUDED.cooldown_end,
                reason = EXCLUDED.reason
        """, (player_id, cooldown_end, reason))

        conn.commit()
        return True

    except Exception as e:
        print(f"Error setting cooldown: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def check_cooldown(player_id):
    """Проверяет кулдаун игрока"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cooldown_end 
            FROM cooldowns 
            WHERE player_id = %s AND cooldown_end > CURRENT_TIMESTAMP
        """, (player_id,))

        result = cursor.fetchone()
        if result:
            return result[0].timestamp()
        return None

    except Exception as e:
        print(f"Error checking cooldown: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def cleanup_cooldowns():
    """Очищает устаревшие кулдауны"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM cooldowns 
            WHERE cooldown_end <= CURRENT_TIMESTAMP
        """)

        conn.commit()
        return True

    except Exception as e:
        print(f"Error cleaning up cooldowns: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


# === Балансировка ===
def balance_teams(players_pool):
    """players_pool = [(name, avg_adr), ...]"""
    best_diff = float("inf")
    best_team1, best_team2 = None, None

    for team1 in itertools.combinations(players_pool, 5):
        team2 = [p for p in players_pool if p not in team1]
        sum1 = sum(p[1] for p in team1)
        sum2 = sum(p[1] for p in team2)
        diff = abs(sum1 - sum2)

        if diff < best_diff:
            best_diff = diff
            best_team1 = (team1, sum1)
            best_team2 = (team2, sum2)

    return best_team1, best_team2, best_diff


def balance_teams_with_history(players_pool):
    """Балансировка с учетом исторической статистики"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем средний ADR для каждого игрока
        player_stats = {}
        for player_name in players_pool:
            cursor.execute("""
                SELECT ROUND(AVG(adr), 1) 
                FROM player_stats ps
                JOIN players p ON ps.player_id = p.id
                WHERE p.first_name = %s
            """, (player_name,))

            result = cursor.fetchone()
            avg_adr = result[0] if result and result[0] else 75.0
            player_stats[player_name] = avg_adr

        # Преобразуем в список для балансировки
        players_with_stats = [(name, adr) for name, adr in player_stats.items()]

        # Стандартная балансировка
        best_diff = float("inf")
        best_team1, best_team2 = None, None

        for team1 in itertools.combinations(players_with_stats, 5):
            team2 = [p for p in players_with_stats if p not in team1]
            sum1 = sum(p[1] for p in team1)
            sum2 = sum(p[1] for p in team2)
            diff = abs(sum1 - sum2)

            if diff < best_diff:
                best_diff = diff
                best_team1 = (team1, sum1)
                best_team2 = (team2, sum2)

        return best_team1, best_team2, best_diff, player_stats

    except Exception as e:
        print(f"Balance with history error: {e}")
        # Fallback к стандартной балансировке
        players_with_stats = [(name, 75.0) for name in players_pool]
        team1, team2, diff = balance_teams(players_with_stats)
        return team1, team2, diff, {name: 75.0 for name in players_pool}


# === Настройка бота ===
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()

# Инициализация базы данных при старте
init_database()


@dp.message(Command("register"))
async def register_player(message: Message):
    try:
        user = message.from_user
        player_id = get_or_create_player(user.id, user.username, user.first_name)

        if player_id:
            await message.answer(
                f"✅ {user.first_name} успешно зарегистрирован!\n\n"
                f"📋 Ваши данные:\n"
                f"• Имя: {user.first_name}\n"
                f"• Username: @{user.username or 'не указан'}\n\n"
                f"Теперь вы можете участвовать в матчах через +кс"
            )
        else:
            await message.answer("❌ Ошибка при регистрации")

    except Exception as e:
        await message.answer("❌ Ошибка при регистрации")
        print(f"Register error: {e}")

# === Команда game ===
@dp.message(Command("game"))
async def game_command(message: Message):
    active_match = get_active_match()

    if active_match:
        await message.answer("❌ Набор игроков уже начат! Используйте /stopgame чтобы завершить текущий набор.")
        return

    players = get_all_players()

    # Создаем новый матч
    match_id = create_match()
    if not match_id:
        await message.answer("❌ Ошибка при создании матча")
        return

    # Отправляем сообщение с тегом всех
    player_mentions = " ".join([f"@{username}" for _, username, _ in players]) if players else "пока нет участников 👥"

    msg = await message.answer(
        f"🎮 Набор на игру открыт! Ставьте +кс чтобы присоединиться!\n\n"
        f"Зарегистрированные игроки: {player_mentions}\n\n"
        f"Текущий пул: 0/10"
    )

    # Пытаемся закрепить сообщение
    try:
        await bot.pin_chat_message(chat_id=message.chat.id, message_id=msg.message_id)
        set_match_pinned_message(match_id, msg.message_id)
    except Exception as e:
        print(f"Не удалось закрепить сообщение: {e}")


# === Команда stopgame ===
@dp.message(Command("stopgame"))
async def stop_game(message: Message):
    active_match = get_active_match()

    if not active_match:
        await message.answer("❌ Сейчас нет активного набора игроков.")
        return

    match_id, pinned_message_id = active_match

    # Деактивируем матч
    if deactivate_match(match_id):
        # Пытаемся открепить сообщение
        try:
            if pinned_message_id:
                await bot.unpin_chat_message(message.chat.id, pinned_message_id)
        except Exception as e:
            print(f"Не удалось открепить сообщение: {e}")

        await message.answer("✅ Набор игроков остановлен. Пул очищен.")
    else:
        await message.answer("❌ Ошибка при остановке матча")


# === Команда для добавления статистики ===
@dp.message(Command("addstats"))
async def add_stats_command(message: Message):
    try:
        # Формат: /addstats @username 120 15 10 5 de_dust2
        parts = message.text.split()
        if len(parts) < 5:
            await message.answer("Формат: /addstats @username kills deaths assists ADR [map]")
            return

        username = parts[1].replace('@', '')
        kills = int(parts[2])
        deaths = int(parts[3])
        adr = float(parts[5])
        assists = int(parts[4])
        map_name = parts[6]

        # Находим игрока
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM players WHERE username = %s", (username,))
        player = cursor.fetchone()

        if not player:
            await message.answer("Игрок не найден")
            return

        player_id = player[0]

        if add_player_stats(player_id, adr, kills, deaths, assists, map_name):
            await message.answer(f"✅ Статистика добавлена для @{username}")
        else:
            await message.answer("❌ Ошибка при добавлении статистики")

    except Exception as e:
        await message.answer("❌ Ошибка формата команды")
        print(f"Add stats error: {e}")


# === Команда для просмотра статистики ===
@dp.message(Command("stats"))
async def stats_command(message: Message):
    try:
        username = None
        if len(message.text.split()) > 1:
            username = message.text.split()[1].replace('@', '')

        conn = get_db_connection()
        cursor = conn.cursor()

        if username:
            # Статистика конкретного игрока
            cursor.execute("""
                SELECT id, first_name FROM players WHERE username = %s
            """, (username,))
            player = cursor.fetchone()

            if not player:
                await message.answer("❌ Игрок не найден")
                return

            player_id, first_name = player
            avg_stats = get_player_average_stats(player_id)
            recent_stats = get_player_stats(player_id, 5)

            if not avg_stats or avg_stats[0] == 0:
                await message.answer(f"📊 У {first_name} еще нет статистики. Добавьте первую запись через /addstats")
                return

            text = f"<b>📊 Статистика {first_name} (@{username})</b>\n\n"
            text += f"🎯 <b>Матчей:</b> {avg_stats[0]}\n"
            text += f"📈 <b>Средний ADR:</b> {avg_stats[1]}\n"
            text += f"🔫 <b>K/D/A:</b> {avg_stats[2]}/{avg_stats[3]}/{avg_stats[4]}\n"
            text += f"⭐ <b>Рейтинг:</b> {avg_stats[5]:.2f}\n\n"
            text += "<b>Последние 5 матчей:</b>\n"

            for stat in recent_stats:
                text += f"• {stat[0].strftime('%d.%m')}: ADR {stat[1]} ({stat[2]}/{stat[3]}/{stat[4]})"
                if stat[6]:  # map
                    text += f" на {stat[6]}"
                text += "\n"

            await message.answer(text, parse_mode="HTML")

        else:
            # Общая статистика всех игроков
            cursor.execute("""
                SELECT p.username, p.first_name, 
                       COUNT(ps.id) as matches,
                       ROUND(AVG(ps.adr)) as avg_adr,
                       ROUND(AVG(ps.kills)) as avg_kills,
                       ROUND(AVG(ps.rating)) as avg_rating
                FROM players p
                LEFT JOIN player_stats ps ON p.id = ps.player_id
                GROUP BY p.id, p.username, p.first_name
                HAVING COUNT(ps.id) > 0
                ORDER BY avg_rating DESC
                LIMIT 10
            """)

            players = cursor.fetchall()

            if not players:
                await message.answer("📊 Нет статистики игроков. Добавьте первую запись через /addstats")
                return

            text = "<b>🏆 Топ-10 игроков по рейтингу</b>\n\n"

            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

            for i, (username, first_name, matches, avg_adr, avg_kills, avg_rating) in enumerate(players):
                if i < 3:
                    medal = medals[i]
                else:
                    medal = f"{i + 1}."

                text += f"{medal} {first_name} - ⭐{avg_rating} (ADR: {avg_adr}, K: {avg_kills}, {matches} матчей)\n"

            text += "\n💡 Используйте <code>/stats @username</code> для детальной статистики"

            await message.answer(text, parse_mode="HTML")

    except Exception as e:
        await message.answer("❌ Ошибка при получении статистики")
        print(f"Stats error: {e}")


# === Команда list ===
@dp.message(Command("list"))
async def list_players_command(message: Message):
    players = get_all_players()
    if not players:
        await message.answer("Список игроков пуст.")
        return

    text_lines = []
    for _, username, first_name in players:
        text_lines.append(f"{first_name} (@{username})")

    text = "👥 Зарегистрированные игроки:\n\n" + "\n".join(text_lines)
    await message.answer(text)


# === Команда pool ===
@dp.message(Command("pool"))
async def show_pool(message: Message):
    active_match = get_active_match()
    if not active_match:
        await message.answer("Нет активного матча. Используйте /game чтобы начать набор.")
        return

    match_id, _ = active_match
    players = get_match_players(match_id)

    if not players:
        await message.answer("Пул пуст. Напиши +кс чтобы присоединиться.")
        return

    player_count = get_match_player_count(match_id)
    pool_list = "\n".join([f"👤 {first_name}" for _, _, first_name, _ in players])

    await message.answer(
        f"🎮 Текущий пул игроков ({player_count}/10):\n\n"
        f"{pool_list}"
    )


# === Запись на игру (+кс) ===
@dp.message(F.text.strip().lower() == "+кс")
async def join_game(message: Message):
    active_match = get_active_match()
    if not active_match:
        await message.answer("❌ Набор игроков не активен. Используйте /game чтобы начать набор.")
        return

    match_id, _ = active_match
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    # Создаем/получаем игрока
    player_id = get_or_create_player(user_id, username, first_name)
    if not player_id:
        await message.answer("❌ Ошибка при регистрации игрока")
        return

    # Проверяем кулдаун
    cooldown_end = check_cooldown(player_id)
    if cooldown_end:
        remaining = int(cooldown_end - time.time())
        await message.answer(f"⏳ Подожди {remaining} секунд перед повторным присоединением.")
        return

    # Проверяем, есть ли уже игрок в пуле
    current_players = get_match_players(match_id)
    if any(player[0] == player_id for player in current_players):
        await message.answer(f"{first_name}, ты уже в пуле!")
        return

    # Проверяем количество игроков
    player_count = get_match_player_count(match_id)
    if player_count >= 10:
        await message.answer("Пул уже заполнен (10 игроков).")
        return

    # Добавляем игрока в матч
    if add_player_to_match(match_id, player_id):
        player_count += 1
        await message.answer(f"✅ {first_name} добавлен в пул! Сейчас в пуле: {player_count}/10")

        # Обновляем закрепленное сообщение
        await update_pinned_pool_message(message.chat.id, match_id)

        # Если набралось 10 игроков - автоматически балансируем
        if player_count == 10:
            await auto_balance_teams(message, match_id)
    else:
        await message.answer("❌ Ошибка при добавлении в пул")


# === Выход из пула (-кс) ===
@dp.message(F.text.strip().lower() == "-кс")
async def leave_game(message: Message):
    active_match = get_active_match()
    if not active_match:
        await message.answer("❌ Набор игроков не активен.")
        return

    match_id, _ = active_match
    user_id = message.from_user.id
    first_name = message.from_user.first_name

    # Находим игрока
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM players WHERE telegram_id = %s", (user_id,))
    player = cursor.fetchone()

    if not player:
        await message.answer("Игрок не найден")
        return

    player_id = player[0]

    # Удаляем игрока из матча
    if remove_player_from_match(match_id, player_id):
        # Устанавливаем кулдаун
        set_cooldown(player_id, 60, "leave_cooldown")

        player_count = get_match_player_count(match_id)
        await message.answer(
            f"❌ {first_name} вышел из пула. Занять слот сможешь через 1 минуту. Сейчас в пуле: {player_count}/10"
        )

        # Обновляем закрепленное сообщение
        await update_pinned_pool_message(message.chat.id, match_id)
    else:
        await message.answer(f"{first_name}, тебя нет в пуле.")


# === Автоматическая балансировка ===
async def auto_balance_teams(message, match_id):
    players = get_match_players(match_id)
    player_names = [first_name for _, _, first_name, _ in players]

    # Балансируем команды
    team1, team2, diff, stats = balance_teams_with_history(player_names)

    t1 = ", ".join([p[0] for p in team1[0]])
    t2 = ", ".join([p[0] for p in team2[0]])

    mention_list = " ".join([f"<a href='tg://user?id={player[1]}'>{player[2]}</a>" for player in players])

    await message.answer(
        f"🎉 Пул собран! 10/10\n\n"
        f"⚔️ Команда 1 (сумма ADR: {team1[1]:.1f}):\n{t1}\n\n"
        f"⚔️ Команда 2 (сумма ADR: {team2[1]:.1f}):\n{t2}\n\n"
        f"📊 Разница между командами: {diff:.1f}\n\n"
        f"👥 Все участники: {mention_list}",
        parse_mode="HTML"
    )


# === Обновление закрепленного сообщения ===
async def update_pinned_pool_message(chat_id, match_id):
    cleanup_cooldowns()

    active_match = get_active_match()
    if not active_match or active_match[0] != match_id:
        return

    players = get_match_players(match_id)
    player_count = len(players)

    pool_list = "\n".join([f"👤 {first_name}" for _, _, first_name, _ in players]) or "Пусто"

    try:
        _, pinned_message_id = active_match
        if pinned_message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=pinned_message_id,
                text=f"🎮 Текущий пул игроков ({player_count}/10):\n\n{pool_list}"
            )
    except Exception as e:
        print(f"Не удалось обновить закрепленное сообщение: {e}")


# === Команда help ===
@dp.message(Command("help"))
async def help_command(message: Message):
    help_text = """
🎮 <b>CS:GO Бот для балансировки команд</b> 🎯

<b>Основные команды:</b>
/game - Начать набор игроков на матч
/stopgame - Остановить набор игроков
/pool - Показать текущий пул игроков
/balance - Сбалансировать команды (когда 10/10)
/reset - Полный сброс игры

<b>Статистика игроков:</b>
/stats - Общая статистика всех игроков
/stats @username - Статистика конкретного игрока
/addstats @username ADR kills deaths assists [map] - Добавить статистику

<b>Управление игроками:</b>
/list - Список всех зарегистрированных игроков

<b>Быстрые действия:</b>
+кс - Присоединиться к текущему матчу
-кс - Выйти из текущего матча

<b>Примеры использования:</b>
• Начать игру: <code>/game</code>
• Добавить статистику: <code>/addstats @ivanov 120 15 10 5 de_dust2</code>
• Посмотреть статистику: <code>/stats @ivanov</code>

📊 <i>Бот автоматически собирает статистику и использует её для балансировки команд!</i>
    """

    await message.answer(help_text, parse_mode="HTML")

@dp.message()
async def unknown_command(message: Message):
    if message.text.startswith('/'):
        await message.answer(
            "❌ Неизвестная команда. Используйте /help для просмотра всех доступных команд\n\n"
            "💡 <b>Основные команды:</b>\n"
            "/game - Начать матч\n"
            "/stats - Статистика\n"
            "/help - Помощь",
            parse_mode="HTML"
        )

# === Main ===
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())