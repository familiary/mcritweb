DROP TABLE IF EXISTS login_attempt;

-- One row per *failed* authentication attempt on /login or /register. Successful
-- attempts are not recorded: the counter exists to meter guessing, and a row per
-- successful login would grow the table with ordinary use for no benefit.
--
-- `remote_addr` is what the throttle blocks on. `username` is recorded so an operator
-- can see a targeted attempt in the log, but is deliberately NOT a lockout key - see
-- db.login_is_throttled() for why.
--
-- `attempted_at` is unix epoch seconds, not a formatted string: this table is only ever
-- compared against "now minus a window", never displayed, and an integer needs no
-- adapter. sqlite3's implicit datetime adapter is deprecated on 3.12+, so storing a
-- datetime here would add a second instance of the problem issue #98 is about.
CREATE TABLE login_attempt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_addr VARCHAR NOT NULL,
    username VARCHAR,
    attempted_at INTEGER NOT NULL
);

CREATE INDEX idx_login_attempt_addr_time ON login_attempt (remote_addr, attempted_at);
