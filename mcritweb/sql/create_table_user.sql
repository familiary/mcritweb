DROP TABLE IF EXISTS user;

CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role VARCHAR NOT NULL,
  registered VARCHAR NOT NULL,
  last_login VARCHAR,
  apitoken VARCHAR,
  -- moved on by every logout, which ends every session issued before it; see
  -- authentication.password_mark
  session_generation INTEGER NOT NULL DEFAULT 0
);
