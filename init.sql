DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'bot_user') THEN
        CREATE USER bot_user WITH PASSWORD 'password';
    END IF;
END
$$;

SELECT 'CREATE DATABASE telegram_bot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'telegram_bot')\gexec

GRANT ALL PRIVILEGES ON DATABASE telegram_bot TO bot_user;

\c telegram_bot;

GRANT ALL ON SCHEMA public TO bot_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO bot_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO bot_user;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO bot_user;
