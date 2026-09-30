import datetime
import os
import secrets
import sqlite3
import time

import click
from flask import current_app, g
from flask.cli import with_appcontext

#: How the two `user` timestamps are written into their VARCHAR columns. Used on the
#: way in and on the way out, because the two have to agree: this is what sqlite3's
#: implicit datetime adapter used to produce, and it is what existing databases hold.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

#: What that adapter produced when the microsecond happened to be exactly 0 -
#: `datetime.isoformat(" ")` drops the fractional part. A row written on that
#: microsecond cannot be read back with TIMESTAMP_FORMAT, so accept it too.
TIMESTAMP_FORMAT_WITHOUT_MICROSECONDS = "%Y-%m-%d %H:%M:%S"


def utc_now():
    """The current time as a timezone-aware UTC datetime.

    `datetime.utcnow()` is deprecated since 3.12 (and returned a *naive* datetime that
    merely happened to hold UTC, which is what made it a trap). See issue #98.
    """
    return datetime.datetime.now(datetime.UTC)


def format_timestamp(value):
    """A datetime as it is stored. Explicit, so sqlite3 never has to adapt one."""
    return value.strftime(TIMESTAMP_FORMAT)


def parse_timestamp(value):
    """A stored timestamp back as a timezone-aware UTC datetime.

    Everything ever written here has been UTC, so the offset is knowledge we have and
    the column does not carry - attach it rather than handing back a naive datetime
    that the next caller has to guess about.
    """
    for timestamp_format in (TIMESTAMP_FORMAT, TIMESTAMP_FORMAT_WITHOUT_MICROSECONDS):
        try:
            parsed = datetime.datetime.strptime(value, timestamp_format)
        except ValueError:
            continue
        return parsed.replace(tzinfo=datetime.UTC)
    raise ValueError(f"Not a stored timestamp: {value!r}")

#: Bytes of entropy behind a per-user API token. 32 gives a 64-character hex string.
APITOKEN_BYTES = 32


def generate_apitoken():
    """A fresh API token.

    This used to be `md5(uuid4().bytes)`. The entropy was always UUID4's rather than
    MD5's, so the tokens were never weak - but MD5 in an authentication path is a
    finding every auditor writes up, and there is no reason to keep it. See issue #100.

    Tokens already issued stay valid: nothing validates their shape, and
    `get_user_by_apitoken` matches on equality.
    """
    return secrets.token_hex(APITOKEN_BYTES)


class UserInfo:

    def __init__(self) -> None:
        self.user_id = None
        self.username = None
        # assume password has been hashed already, e.g. via generate_password_hash from werkzeug.security
        self.password = None
        self.role = None
        self.registered = None
        self.last_login = None
        self.apitoken = None

    @classmethod
    def fromDb(cls, user_id=None, username=None):
        db = get_db()
        cursor = db.cursor()
        record = None
        if user_id is not None:
            record = cursor.execute("SELECT * FROM user WHERE id = ?;", (user_id,)).fetchone()
        elif username is not None:
            record = cursor.execute("SELECT * FROM user WHERE username = ?;", (username,)).fetchone()
        if record:
            user_info = cls()
            user_info.user_id = record["id"]
            user_info.username = record["username"]
            user_info.password = record["password"]
            user_info.role = record["role"]
            user_info.registered = parse_timestamp(record["registered"])
            user_info.last_login = "no login"
            if record["last_login"] != "no login":
                user_info.last_login = parse_timestamp(record["last_login"])
            user_info.apitoken = record["apitoken"]
        else:
            user_info = None
        return user_info
    
    def saveToDb(self, withPassword=False):
        database = get_db()
        # query to see if row exists
        record = database.execute("SELECT * FROM user WHERE id = ?;", (self.user_id,)).fetchone()
        if record:
            database.execute("UPDATE user SET username = ? WHERE id = ?;",(self.username, self.user_id,))
            if withPassword:
                database.execute("UPDATE user SET password = ? WHERE id = ?;",(self.password, self.user_id,))
            database.execute("UPDATE user SET role = ? WHERE id = ?;",(self.role, self.user_id,))
            if isinstance(self.registered, datetime.datetime):
                database.execute("UPDATE user SET registered = ? WHERE id = ?;",(format_timestamp(self.registered), self.user_id,))
            if isinstance(self.last_login, datetime.datetime):
                database.execute("UPDATE user SET last_login = ? WHERE id = ?;",(format_timestamp(self.last_login), self.user_id,))
            database.execute("UPDATE user SET apitoken = ? WHERE id = ?;",(self.apitoken, self.user_id,))
        else:
            database.execute(
                "INSERT INTO user (username, password, role, registered, last_login, apitoken) VALUES (?,?,?,?,?,?)",
                # formatted here rather than handed over as a datetime: sqlite3's
                # implicit adapter is deprecated as of 3.12, and it wrote a value
                # fromDb could not always read back. See issue #98.
                (self.username, self.password, self.role, format_timestamp(utc_now()), 'no login', self.apitoken),
            )
        database.commit()
    
    @property
    def registration_date(self):
        return self.registered.strftime("%Y-%m-%d")
    
def get_stored_password_hash_method():
    """The hashing method the user table's passwords were made with, or None.

    check_password_hash costs whatever the *stored* hash asks for, not whatever
    generate_password_hash would pick today, and werkzeug's default has moved
    (pbkdf2:sha256:260000 -> 600000 -> scrypt) across the versions this app has been
    pinned to. Werkzeug writes the method into the hash ahead of the first '$', so the
    answer can simply be read off a row. See issue #101 and _spend_a_password_check.

    The *most common* method, not the first row's. A table carrying a mix - accounts
    from before a werkzeug upgrade alongside accounts registered after it - has no
    single right answer, and one dummy cannot match two methods. Picking the majority
    leaves the smallest set of accounts distinguishable; picking an arbitrary row (a
    bare LIMIT 1) leaves whichever set that row does not belong to, which on an upgraded
    instance is every account created since the upgrade.

    Read afresh on every call, deliberately. A cached answer would have to be dropped
    by every write to the password column - registration, the rehash on login, a
    password change, deleting a user - and by the ones this process never sees: a
    second worker, `flask init-db`, a restored database file. A stale answer reopens
    the timing gap without anything failing.

    The price is paid on exactly the path this disguises: the query runs only when
    the username is absent, so on a large user table it adds a small, repeatable delay
    that a wrong password does not have - about 2 ms at 5,000 users. That is still far
    below the hash check it precedes (66-150 ms), and the login throttle meters how
    often anyone can ask. See issue #190.
    """
    record = get_db().execute(
        """
        SELECT substr(password, 1, instr(password, '$') - 1) AS method, COUNT(*) AS rows_with_it
        FROM user
        WHERE instr(password, '$') > 1
        GROUP BY method
        ORDER BY rows_with_it DESC
        LIMIT 1;
        """
    ).fetchone()
    if record is None or not record["method"]:
        return None
    return record["method"]


def get_all_user_info():
    all_user_infos = []
    database = get_db() 
    # TODO this can be refactored to using cursor results if we add a .fromDict() to UserInfo
    for user in database.execute('SELECT * FROM user').fetchall():
        all_user_infos.append(UserInfo.fromDb(user_id=user["id"]))
    return all_user_infos
    

class ServerInfo:

    def __init__(self) -> None:
        self.url = None
        self.operation_mode = None
        self.registration_token = None
        self.server_token = None
        self.server_uuid = None
        self.server_version = None

    def __str__(self) -> str:
        return f"ServerInfo(url='{self.url}', operation_mode='{self.operation_mode}', registration_token='{self.registration_token}', server_token='{self.server_token}', server_uuid='{self.server_uuid}', server_version='{self.server_version}')"

    @classmethod
    def fromDb(cls):
        db = get_db()
        record = db.execute("SELECT * FROM server;").fetchone()
        if record:
            server_info = cls()
            server_info.url = record["url"]
            server_info.operation_mode = record["operation_mode"]
            server_info.registration_token = record["registration_token"]
            server_info.server_token = record["server_token"]
            server_info.server_uuid = record["server_uuid"]
            server_info.server_version = record["server_version"]
        else:
            server_info = None
        return server_info
    
    def saveToDb(self):
        # whatever get_server_info() handed out earlier in this request is about to be
        # out of date - dropped first, so a failed write cannot leave it believed either
        g.pop('server_info', None)
        database = get_db()
        # query to see if row exists
        record = database.execute("SELECT rowid FROM server LIMIT 1;").fetchone()
        if record:
            database.execute(
                "UPDATE server SET url = ?, operation_mode = ?, registration_token = ?, server_token = ?, server_uuid = ?, server_version = ? WHERE rowid = ?",
                (self.url, self.operation_mode, self.registration_token, self.server_token, self.server_uuid, self.server_version, record["rowid"]),
            )
        else:
            database.execute(
                "INSERT INTO server (url, operation_mode, registration_token, server_token, server_uuid, server_version) VALUES (?,?,?,?,?,?)",
                (self.url, self.operation_mode, self.registration_token, self.server_token, self.server_uuid, self.server_version),
            )
        database.commit()


def get_server_info():
    """The server row, read once per request.

    The operation-mode hook reads it, and so do get_server_url and get_server_token,
    which the reachability probe and the client factory each call - five reads of the
    same row on a page that probes the backend and builds a client. Kept on `g`, so it
    lives no longer than the request; ServerInfo.saveToDb drops it, so a request that
    changes the settings reads its own write. See issue #190.
    """
    if 'server_info' not in g:
        g.server_info = ServerInfo.fromDb()
    return g.server_info


class UserFilters:

    def __init__(self) -> None:
        self.user_id = None
        self.filter_direct_min_score = None
        self.filter_direct_nonlib_min_score = None
        self.filter_frequency_min_score = None
        self.filter_frequency_nonlib_min_score = None
        self.filter_unique_only = None
        self.filter_exclude_own_family = None
        self.filter_function_min_score = None
        self.filter_function_max_score = None
        self.filter_max_num_families = None
        self.filter_exclude_library = None
        self.filter_exclude_pic = None
        # this is never stored as preference in the DB as its not as generic as the others
        self.filter_family_name = None
        self.filter_function_offset = None
        self.filter_min_num_samples = None
        self.filter_max_num_samples = None

    @classmethod
    def fromDb(cls, user_id):
        db = get_db()
        cursor = db.cursor()
        record = cursor.execute("SELECT * FROM user_filters WHERE user_id = ?;", (user_id,)).fetchone()
        if record:
            user_filter_info = cls()
            user_filter_info.user_id = user_id
            user_filter_info.filter_direct_min_score = record["filter_direct_min_score"]
            user_filter_info.filter_direct_nonlib_min_score = record["filter_direct_nonlib_min_score"]
            user_filter_info.filter_frequency_min_score = record["filter_frequency_min_score"]
            user_filter_info.filter_frequency_nonlib_min_score = record["filter_frequency_nonlib_min_score"]
            user_filter_info.filter_unique_only = record["filter_unique_only"]
            user_filter_info.filter_exclude_own_family = record["filter_exclude_own_family"]
            user_filter_info.filter_function_min_score = record["filter_function_min_score"]
            user_filter_info.filter_function_max_score = record["filter_function_max_score"]
            user_filter_info.filter_max_num_families = record["filter_max_num_families"]
            user_filter_info.filter_exclude_library = record["filter_exclude_library"]
            user_filter_info.filter_exclude_pic = record["filter_exclude_pic"]
            # remainder set implicitly to None
        else:
            user_filter_info = None
        return user_filter_info
    
    @classmethod
    def fromDict(cls, user_id, filter_dict):
        user_filter_info = cls()
        user_filter_info.user_id = user_id
        user_filter_info.filter_direct_min_score = 0 if "filter_direct_min_score" not in filter_dict or filter_dict["filter_direct_min_score"] == 0 else filter_dict["filter_direct_min_score"]
        user_filter_info.filter_direct_nonlib_min_score = 0 if "filter_direct_nonlib_min_score" not in filter_dict or filter_dict["filter_direct_nonlib_min_score"] == 0 else filter_dict["filter_direct_nonlib_min_score"]
        user_filter_info.filter_frequency_min_score = 0 if "filter_frequency_min_score" not in filter_dict or filter_dict["filter_frequency_min_score"] == 0 else filter_dict["filter_frequency_min_score"]
        user_filter_info.filter_frequency_nonlib_min_score = 0 if "filter_frequency_nonlib_min_score" not in filter_dict or filter_dict["filter_frequency_nonlib_min_score"] == 0 else filter_dict["filter_frequency_nonlib_min_score"]
        user_filter_info.filter_unique_only = True if "filter_unique_only" in filter_dict and filter_dict["filter_unique_only"] else False
        user_filter_info.filter_exclude_own_family = True if "filter_exclude_own_family" in filter_dict and filter_dict["filter_exclude_own_family"] else False
        user_filter_info.filter_function_min_score = 0 if "filter_function_min_score" not in filter_dict or filter_dict.get("filter_function_min_score", 0) == 0 else filter_dict["filter_function_min_score"]
        user_filter_info.filter_function_max_score = 100 if "filter_function_max_score" not in filter_dict or filter_dict.get("filter_function_max_score", 100) == 100 else filter_dict["filter_function_max_score"]
        user_filter_info.filter_max_num_families = 0 if "filter_max_num_families" not in filter_dict or filter_dict.get("filter_max_num_families", 0) == 0 else filter_dict["filter_max_num_families"]
        user_filter_info.filter_exclude_library = True if "filter_exclude_library" in filter_dict and filter_dict["filter_exclude_library"] else False
        user_filter_info.filter_exclude_pic = True if "filter_exclude_pic" in filter_dict and filter_dict["filter_exclude_pic"] else False
        # 
        user_filter_info.filter_family_name = None if "filter_family_name" not in filter_dict or filter_dict["filter_family_name"] == "" else filter_dict["filter_family_name"]
        user_filter_info.filter_function_offset = None if "filter_function_offset" not in filter_dict or filter_dict["filter_function_offset"] < 0 else filter_dict["filter_function_offset"]
        user_filter_info.filter_min_num_samples = 0 if "filter_min_num_samples" not in filter_dict or filter_dict.get("filter_min_num_samples", 0) == 0 else filter_dict["filter_min_num_samples"]
        user_filter_info.filter_max_num_samples = 0 if "filter_max_num_samples" not in filter_dict or filter_dict.get("filter_max_num_samples", 0) == 0 else filter_dict["filter_max_num_samples"]
        return user_filter_info

    def toDict(self):
        return {
            "user_id": self.user_id,
            "filter_direct_min_score": self.filter_direct_min_score,
            "filter_direct_nonlib_min_score": self.filter_direct_nonlib_min_score,
            "filter_frequency_min_score": self.filter_frequency_min_score,
            "filter_frequency_nonlib_min_score": self.filter_frequency_nonlib_min_score,
            "filter_unique_only": self.filter_unique_only,
            "filter_exclude_own_family": self.filter_exclude_own_family,
            "filter_function_min_score": self.filter_function_min_score,
            "filter_function_max_score": self.filter_function_max_score,
            "filter_max_num_families": self.filter_max_num_families,
            "filter_exclude_library": self.filter_exclude_library,
            "filter_exclude_pic": self.filter_exclude_pic,
            #
            "filter_family_name": self.filter_family_name,
            "filter_function_offset": self.filter_function_offset,
            "filter_min_num_samples": self.filter_min_num_samples,
            "filter_max_num_samples": self.filter_max_num_samples,
        }
    
    def saveToDb(self):
        database = get_db()
        # query to see if row exists
        record = database.execute("SELECT * FROM user_filters WHERE user_id = ?;", (self.user_id,)).fetchone()
        if record:
            # NOTE: the WHERE clause is load-bearing - without it every user's filters are
            # overwritten with this user's values (see issue #81)
            database.execute(
                "UPDATE user_filters SET filter_direct_min_score = ?, filter_direct_nonlib_min_score = ?, filter_frequency_min_score = ?, filter_frequency_nonlib_min_score = ?, filter_unique_only = ?, filter_exclude_own_family = ?, filter_function_min_score = ?, filter_function_max_score = ?, filter_max_num_families = ?, filter_exclude_library = ?, filter_exclude_pic = ? WHERE user_id = ?;",
                (min(100, max(0, self.filter_direct_min_score)),
                min(100, max(0, self.filter_direct_nonlib_min_score)),
                min(100, max(0, self.filter_frequency_min_score)),
                min(100, max(0, self.filter_frequency_nonlib_min_score)),
                1 if self.filter_unique_only else 0,
                1 if self.filter_exclude_own_family else 0,
                min(100, max(0, self.filter_function_min_score)),
                min(100, max(0, self.filter_function_max_score)),
                max(0, self.filter_max_num_families),
                1 if self.filter_exclude_library else 0,
                1 if self.filter_exclude_pic else 0,
                self.user_id,
            ))
        else:
            # insert inital values
            database.execute("INSERT INTO user_filters (user_id, filter_direct_min_score, filter_direct_nonlib_min_score, filter_frequency_min_score, filter_frequency_nonlib_min_score, filter_unique_only, filter_exclude_own_family, filter_function_min_score, filter_function_max_score, filter_max_num_families, filter_exclude_library, filter_exclude_pic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.user_id,
                min(100, max(0, self.filter_direct_min_score)),
                min(100, max(0, self.filter_direct_nonlib_min_score)),
                min(100, max(0, self.filter_frequency_min_score)), 
                min(100, max(0, self.filter_frequency_nonlib_min_score)), 
                1 if self.filter_unique_only else 0, 
                1 if self.filter_exclude_own_family else 0, 
                min(100, max(0, self.filter_function_min_score)),
                min(100, max(0, self.filter_function_max_score)), 
                max(0, self.filter_max_num_families), 
                1 if self.filter_exclude_library else 0, 
                1 if self.filter_exclude_pic else 0,
            ))
        database.commit()


class UserColumnSettings:

    # Define the default column configuration template
    _default_settings = {
        "family_table": {
            "active": ["family_id", "family_name", "num_samples", "num_functions", "is_library"],
            "available": []
        },
        "samples_table": {
            "active": ["sample_id", "sha256", "family", "version", "filename", "bitness", "num_functions", "is_library"],
            "available": []
        },
        "functions_table": {
            "active": ["function_id", "family_id", "sample_id", "pic_hash", "has_minhash", "offset", "function_name", "num_instructions", "num_blocks"],
            "available": []
        },
        "result_family_table": {
            "active": ["family_name", "version", "sample_id", "sha256", "filename", "num_functions", "num_minhash", "num_pichash", "direct_score", "direct_nonlib_score", "frequency_score", "frequency_nonlib_score", "uniq_score"],
            "available": []
        },
        "result_function_unfiltered_table": {
            "active": ["matched_function_id", "offset", "num_bytes", "num_matched_families", "num_matched_samples", "num_matched_functions", "best_score", "num_minhash", "num_pichash", "is_library_match", "is_unique_match"],
            "available": []
        },
        "result_function_sample_filtered_table": {
            "active": ["function_id_a", "offset_a", "offset_b", "function_id_b", "num_bytes", "best_score", "is_minhash_match", "is_pichash_match", "is_library_match", "is_unique_match"],
            "available": []
        },
        "result_function_function_filtered_table": {
            "active": ["function_id_a", "offset_a", "offset_b", "function_id_b", "family_name_b", "sample_id_b", "best_score", "is_minhash_match", "is_pichash_match", "is_library_match", "is_unique_match"],
            "available": []
        },
    }

    def __init__(self, user_id=None) -> None:
        self.user_id = user_id if user_id is not None else None

        # Dynamically create attributes based on the template with default positions
        for table_name, columns in self._default_settings.items():
            for position, column in enumerate(columns["active"]):
                attr_name = f"{table_name}_{column}"
                setattr(self, attr_name, position)  # Default to position (0, 1, 2, ...)

    @classmethod
    def fromDb(cls, user_id):
        db = get_db()
        cursor = db.cursor()
        record = cursor.execute("SELECT * FROM user_column_settings WHERE user_id = ?;", (user_id,)).fetchone()
        if record:
            user_column_settings = cls()
            user_column_settings.user_id = user_id
            
            # Set all column attributes from database record
            for table_name, columns in user_column_settings._default_settings.items():
                for default_position, column in enumerate(columns["active"]):
                    attr_name = f"{table_name}_{column}"
                    if attr_name in record.keys():
                        setattr(user_column_settings, attr_name, record[attr_name])
                    else:
                        setattr(user_column_settings, attr_name, default_position)  # Default to position
        else:
            user_column_settings = None
        return user_column_settings
    
    @classmethod
    def fromDict(cls, user_id, settings_dict):
        user_column_settings = cls()
        user_column_settings.user_id = user_id
        
        # Process the column settings dictionary
        if settings_dict and "column_settings" in settings_dict:
            column_settings = settings_dict["column_settings"]

            # Reset all to -1 (not selected) first
            for table_name, columns in user_column_settings._default_settings.items():
                for column in columns["active"]:
                    attr_name = f"{table_name}_{column}"
                    setattr(user_column_settings, attr_name, -1)
            
            # Set active columns with their positions based on input
            for table_name, table_config in column_settings.items():
                if table_name in user_column_settings._default_settings:
                    active_columns = table_config.get("active", [])
                    for position, column in enumerate(active_columns):
                        attr_name = f"{table_name}_{column}"
                        if hasattr(user_column_settings, attr_name):
                            setattr(user_column_settings, attr_name, position)

        else:
            # If no column settings were provided, use defaults
            for table_name, columns in user_column_settings._default_settings.items():
                for default_position, column in enumerate(columns["active"]):
                    attr_name = f"{table_name}_{column}"
                    setattr(user_column_settings, attr_name, default_position)

        return user_column_settings

    def _sanitize_column_settings(self):
        """Sanitize column settings to ensure proper integer positions and ordering"""
        for table_name, columns in self._default_settings.items():
            # Collect all active columns with their positions
            active_positions = []
            
            for default_position, column in enumerate(columns["active"]):
                attr_name = f"{table_name}_{column}"
                current_value = getattr(self, attr_name, default_position)
                
                # Convert None and non-integers to -1
                if current_value is None or not isinstance(current_value, int):
                    setattr(self, attr_name, -1)
                elif current_value >= 0:
                    # Collect positions for reordering
                    active_positions.append((current_value, column))
            
            # Sort by position and reassign sequential positions starting from 0
            active_positions.sort(key=lambda x: x[0])
            
            # Special handling for result_family_table score pairs
            if table_name == "result_family_table":
                active_positions = self._enforce_score_pair_ordering(active_positions)
            
            for new_position, (old_position, column) in enumerate(active_positions):
                attr_name = f"{table_name}_{column}"
                setattr(self, attr_name, new_position)

    def _enforce_score_pair_ordering(self, active_positions):
        """Ensure that score pairs are placed next to each other in result_family_table"""
        # Convert to a list of column names for easier manipulation
        columns = [column for position, column in active_positions]
        
        # Handle direct_score and direct_nonlib_score pair
        if "direct_score" in columns and "direct_nonlib_score" in columns:
            direct_score_idx = columns.index("direct_score")
            direct_nonlib_score_idx = columns.index("direct_nonlib_score")
            
            # If they're not adjacent, move direct_nonlib_score right after direct_score
            if direct_nonlib_score_idx != direct_score_idx + 1:
                # Remove direct_nonlib_score from its current position
                columns.pop(direct_nonlib_score_idx)
                # Insert it right after direct_score (adjust index if we removed from before)
                insert_idx = direct_score_idx + 1
                if direct_nonlib_score_idx < direct_score_idx:
                    insert_idx = direct_score_idx  # direct_score index shifted down
                columns.insert(insert_idx, "direct_nonlib_score")
        
        # Handle frequency_score and frequency_nonlib_score pair
        if "frequency_score" in columns and "frequency_nonlib_score" in columns:
            frequency_score_idx = columns.index("frequency_score")
            frequency_nonlib_score_idx = columns.index("frequency_nonlib_score")
            
            # If they're not adjacent, move frequency_nonlib_score right after frequency_score
            if frequency_nonlib_score_idx != frequency_score_idx + 1:
                # Remove frequency_nonlib_score from its current position
                columns.pop(frequency_nonlib_score_idx)
                # Insert it right after frequency_score (adjust index if we removed from before)
                insert_idx = frequency_score_idx + 1
                if frequency_nonlib_score_idx < frequency_score_idx:
                    insert_idx = frequency_score_idx  # frequency_score index shifted down
                columns.insert(insert_idx, "frequency_nonlib_score")
        
        # Convert back to (position, column) pairs
        return [(position, column) for position, column in enumerate(columns)]

    def toDict(self):
        # Sanitize data before processing
        self._sanitize_column_settings()
        
        result = {
            "user_id": self.user_id,
        }
        
        # Add all column settings with their positions
        for table_name, columns in self._default_settings.items():
            for default_position, column in enumerate(columns["active"]):
                attr_name = f"{table_name}_{column}"
                result[attr_name] = getattr(self, attr_name, default_position)
        
        return result
    
    def toUserColumnSettings(self):
        """Convert to the format expected by the frontend"""
        # Sanitize data before processing
        self._sanitize_column_settings()
        
        result = {}
        
        for table_name, columns in self._default_settings.items():
            result[table_name] = {
                "active": [],
                "available": []
            }
            
            # Create list of (position, column) pairs for active columns
            active_columns_with_positions = []
            
            for default_position, column in enumerate(columns["active"]):
                attr_name = f"{table_name}_{column}"
                position = getattr(self, attr_name, default_position)
                
                if position >= 0:
                    active_columns_with_positions.append((position, column))
                else:
                    result[table_name]["available"].append(column)
            
            # Sort by position and extract column names
            active_columns_with_positions.sort(key=lambda x: x[0])
            result[table_name]["active"] = [column for position, column in active_columns_with_positions]
        
        return result
    
    def saveToDb(self):
        # Sanitize data before saving
        self._sanitize_column_settings()
        
        database = get_db()
        # Query to see if row exists
        record = database.execute("SELECT * FROM user_column_settings WHERE user_id = ?;", (self.user_id,)).fetchone()
        
        # Build column names and values dynamically
        column_names = []
        column_values = []
        
        for table_name, columns in self._default_settings.items():
            for default_position, column in enumerate(columns["active"]):
                attr_name = f"{table_name}_{column}"
                column_names.append(attr_name)
                position = getattr(self, attr_name, default_position)
                column_values.append(position)
        
        if record:
            # Update existing record
            update_clauses = [f"{name} = ?" for name in column_names]
            query = f"UPDATE user_column_settings SET {', '.join(update_clauses)} WHERE user_id = ?;"
            database.execute(query, column_values + [self.user_id])
        else:
            # Insert new record
            placeholders = ', '.join(['?'] * (len(column_names) + 1))  # +1 for user_id
            columns_str = ', '.join(['user_id'] + column_names)
            query = f"INSERT INTO user_column_settings ({columns_str}) VALUES ({placeholders});"
            database.execute(query, [self.user_id] + column_values)
        
        database.commit()


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with current_app.open_resource('sql' + os.sep + 'create_table_user.sql') as f:
        db.executescript(f.read().decode('utf8'))
    with current_app.open_resource('sql' + os.sep + 'create_table_user_filters.sql') as f:
        db.executescript(f.read().decode('utf8'))
    with current_app.open_resource('sql' + os.sep + 'create_table_user_column_settings.sql') as f:
        db.executescript(f.read().decode('utf8'))
    with current_app.open_resource('sql' + os.sep + 'create_table_server.sql') as f:
        db.executescript(f.read().decode('utf8'))
    with current_app.open_resource('sql' + os.sep + 'create_table_login_attempt.sql') as f:
        db.executescript(f.read().decode('utf8'))
    with current_app.open_resource('sql' + os.sep + 'create_table_query_upload.sql') as f:
        db.executescript(f.read().decode('utf8'))

@click.command('init-db')
@with_appcontext
def init_db_command():
    """Clear the existing data and create new tables."""
    print("Starting database initialization.")
    init_db()
    click.echo('Initialized the database.')

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)


def migrate(app_context):
    # custom connect since we are before app initialization
    db = sqlite3.connect(
            app_context.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
    # close on every exit path, including the early return and any failing migration
    try:
        # check if DB was initialized before taking further action.
        try:
            db.execute('SELECT * FROM user').fetchone()
        except sqlite3.OperationalError:
            print("Database tables not initialized yet, skipping migrations.")
            return
        # since version v0.11.0, users can have default filter, ensure table exists
        table_needs_creation = False
        try:
            db.execute('SELECT * FROM user_filters').fetchone()
        except sqlite3.OperationalError:
            table_needs_creation = True
        if table_needs_creation:
            with app_context.open_resource('sql' + os.sep + 'create_table_user_filters.sql') as f:
                db.executescript(f.read().decode('utf8'))
            print("EXECUTED MIGRATION: CREATED TABLE USER_FILTERS")
        # since version v0.12.0, users have an apitoken, ensure it exists (initializd)
        cursor = db.cursor()
        user_table_columns = list(map(lambda x: x[0], db.execute('SELECT * FROM user').description))
        if "apitoken" not in user_table_columns:
            db.execute('ALTER TABLE user ADD apitoken VARCHAR')
            cursor = db.cursor()
            user_ids = []
            for record in cursor.execute("select * from user;").fetchall():
                user_ids.append(record[0])
            for user_id in user_ids:
                generated_apitoken = generate_apitoken()
                db.execute("UPDATE user SET apitoken = ? WHERE id = ?", (generated_apitoken, user_id))
                # the token itself is deliberately not printed: it authenticates its
                # owner to /api, and this line goes to the container log
                print(f"EXECUTED MIGRATION: ADD APITOKEN TO USER_ID {user_id} FROM TABLE USER")
        # since version v1.2.10, we have an additional server_token field, ensure it exists (empty)
        server_table_columns = list(map(lambda x: x[0], db.execute('SELECT * FROM server').description))
        if "server_token" not in server_table_columns:
            db.execute('ALTER TABLE server ADD server_token VARCHAR')
            db.execute("UPDATE server SET server_token = ?;", ("",))
            print("EXECUTED MIGRATION: ADD SERVER_TOKEN TO TABLE SERVER")
        # since version v1.4.0, we have user_column_settings, ensure table exists
        try:
            db.execute('SELECT * FROM user_column_settings').fetchone()
        except sqlite3.OperationalError:
            with app_context.open_resource('sql' + os.sep + 'create_table_user_column_settings.sql') as f:
                db.executescript(f.read().decode('utf8'))
            print("EXECUTED MIGRATION: CREATED TABLE USER_COLUMN_SETTINGS")
        # since v1.4.9, failed logins are metered, ensure the table exists
        attempt_table_needs_creation = False
        try:
            db.execute('SELECT * FROM login_attempt').fetchone()
        except sqlite3.OperationalError:
            attempt_table_needs_creation = True
        if attempt_table_needs_creation:
            with app_context.open_resource('sql' + os.sep + 'create_table_login_attempt.sql') as f:
                db.executescript(f.read().decode('utf8'))
            print("EXECUTED MIGRATION: CREATED TABLE LOGIN_ATTEMPT")
        # since query results name the file they were uploaded as (#40), ensure the table exists
        try:
            db.execute('SELECT * FROM query_upload').fetchone()
        except sqlite3.OperationalError:
            with app_context.open_resource('sql' + os.sep + 'create_table_query_upload.sql') as f:
                db.executescript(f.read().decode('utf8'))
            print("EXECUTED MIGRATION: CREATED TABLE QUERY_UPLOAD")

        db.commit()
    finally:
        db.close()


#: How many failed attempts one address may make inside LOGIN_ATTEMPT_WINDOW before it
#: is refused. Deliberately generous: a person who has forgotten which of two passwords
#: they used should never meet this, and an attacker is not meaningfully inconvenienced
#: by 10 versus 5 - what stops unmetered guessing is the window, not the count.
LOGIN_ATTEMPT_LIMIT = 10

#: Seconds. Also how long a blocked address stays blocked, since the count is always
#: taken over the trailing window rather than held as a lockout flag - so a block
#: expires on its own and there is no state to clear or unlock.
LOGIN_ATTEMPT_WINDOW = 900


def record_failed_login(remote_addr, username):
    """Record one failed attempt, and drop attempts that have aged out of the window.

    Pruning here rather than on a timer keeps the table bounded without a scheduler:
    the only thing that grows it is the same call that trims it.
    """
    now = int(time.time())
    db = get_db()
    db.execute(
        "INSERT INTO login_attempt (remote_addr, username, attempted_at) VALUES (?, ?, ?)",
        (remote_addr or "", username or "", now),
    )
    db.execute("DELETE FROM login_attempt WHERE attempted_at < ?", (now - LOGIN_ATTEMPT_WINDOW,))
    db.commit()


def count_recent_login_failures(remote_addr, username=None):
    """Failures from this address in the trailing window; optionally for one username."""
    since = int(time.time()) - LOGIN_ATTEMPT_WINDOW
    db = get_db()
    if username is None:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM login_attempt WHERE remote_addr = ? AND attempted_at >= ?",
            (remote_addr or "", since),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM login_attempt "
            "WHERE remote_addr = ? AND username = ? AND attempted_at >= ?",
            (remote_addr or "", username or "", since),
        ).fetchone()
    return row["n"] if row else 0


def login_is_throttled(remote_addr):
    """Whether this address has spent its attempts for the current window.

    Keyed on the address only, never on the username, and that is the whole design
    decision issue #101 asks to make explicit. A per-account lockout hands an attacker
    a denial of service against any account whose name they know: fail four times
    against `admin` and the real admin is locked out. Metering the *source* costs an
    attacker their own budget instead.

    The username is still counted - see `count_recent_login_failures` - but only so the
    log can say that attempts are aimed at one account, which is what an operator needs
    to see. It never blocks.
    """
    return count_recent_login_failures(remote_addr) >= LOGIN_ATTEMPT_LIMIT


def clear_login_failures(remote_addr):
    """Forget this address's failures. Called on a successful authentication, so an
    ordinary person who mistyped their password a few times does not carry the count
    around for the rest of the window."""
    db = get_db()
    db.execute("DELETE FROM login_attempt WHERE remote_addr = ?", (remote_addr or "",))
    db.commit()


def is_first_user():
    """True while the user table is empty. Asked on every request, so it only asks
    whether a row exists rather than reading them all - see issue #190."""
    return get_db().execute("SELECT 1 FROM user LIMIT 1;").fetchone() is None

def get_registration_token():
    registration_token = None
    db = get_db()
    cursor = db.cursor()
    record = cursor.execute("select registration_token from server;").fetchone()
    if record:  
        registration_token = record["registration_token"]
    return registration_token

def get_operation_mode():
    operation_mode = None
    db = get_db()
    cursor = db.cursor()
    record = cursor.execute("select operation_mode from server;").fetchone()
    if record:  
        operation_mode = record["operation_mode"]
    return operation_mode

def get_user_by_apitoken(apitoken):
    user_id = None
    db = get_db()
    cursor = db.cursor()
    record = cursor.execute("SELECT * FROM user WHERE apitoken = ?;", (apitoken,)).fetchone()
    if record is not None:
        user_id = record["id"]
    return user_id

def get_username_by_apitoken(apitoken):
    username = None
    db = get_db()
    cursor = db.cursor()
    record = cursor.execute("SELECT * FROM user WHERE apitoken = ?;", (apitoken,)).fetchone()
    if record is not None:
        username = record["username"]
    return username

#: A query filename is display metadata, never a path. Cap it so a hostile upload can
#: neither bloat the table nor push everything else off the result page.
MAX_QUERY_FILENAME_LENGTH = 255

def _displayable_filename(filename):
    """Reduce an uploaded filename to what may be shown to another user.

    `isprintable()` drops control characters and the invisible formatting characters
    that let one name be dressed as another. Path separators are kept - the value is
    never used to build a path here, and mangling them would show the uploader a name
    they do not recognize.
    """
    if not filename or not isinstance(filename, str):
        return ""
    return "".join(character for character in filename if character.isprintable()).strip()[:MAX_QUERY_FILENAME_LENGTH]

def remember_query_filename(job_id, filename):
    """Record the name a query was uploaded under, so its result page can show it.

    None of the backend's query endpoints accepts a filename, so this is the only
    place the name is ever kept. Keyed by job id: the backend deduplicates a repeated
    query onto the job it already has, and the name kept is the first one seen for it.
    """
    filename = _displayable_filename(filename)
    if not job_id or not filename:
        return
    db = get_db()
    db.execute("INSERT OR IGNORE INTO query_upload (job_id, filename) VALUES (?, ?);", (str(job_id), filename))
    db.commit()

def get_query_filename(job_id):
    """The name a query job's upload carried, or None if this instance never saw it."""
    if not job_id:
        return None
    db = get_db()
    cursor = db.cursor()
    record = cursor.execute("SELECT filename FROM query_upload WHERE job_id = ?;", (str(job_id),)).fetchone()
    return record["filename"] if record is not None else None
