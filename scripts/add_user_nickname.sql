ALTER TABLE IF EXISTS users
    ADD COLUMN IF NOT EXISTS nickname VARCHAR(50);

UPDATE users
SET nickname = username
WHERE nickname IS NULL OR BTRIM(nickname) = '';
