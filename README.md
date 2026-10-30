# MCRITweb
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/familiary/mcritweb)

MCRITweb is a Flask-based user interface for the [MinHash-based Code Recognition & Investigation Toolkit (MCRIT)](https://github.com/danielplohmann/mcrit).  
MCRITweb has been developed by Daniel Enders, Manuel Blatt, and Daniel Plohmann.

## Installation

We highly recommend using the dockerized deployment available at [docker-mcrit](https://github.com/danielplohmann/docker-mcrit).

If you instead want to go for a direct installation, a few dependencies have to be installed.  
First, ensure that Python 3.12+ is available, then install MCRITweb with its dependencies:
```bash
# install python, then MCRITweb and its dependencies
$ sudo apt install python3 python3-pip
$ pip install -e .
```

Obviously, also make sure that the backend [MCRIT](https://github.com/danielplohmann/mcrit) is fully installed, configured, and running.


## Usage

### Dockerized Usage

We highly recommend to use the fully packaged [docker-mcrit](https://github.com/danielplohmann/docker-mcrit) for trivial deployment and usage.  
First and foremost, this will ensure that you have fully compatible versions across all components.

### Standalone Usage

If you instead want to run MCRITweb as a standalone tool, the following steps will enable this:

Running flask commands requires you to set environment variables in your shell:  
`$ source ./flask_env.sh`

before the first usage, create an empty database:   
`$ flask init-db`

and then to run MCRITweb, execute:  
`$ flask run`

Note that most functionality of MCRITweb will only work if an MCRIT backend is configured and available.

MCRITweb gzips its pages, stylesheets, scripts and JSON for browsers that accept it, so a bare `flask run` or waitress deployment is not stuck with uncompressed transfers. The reference `docker-mcrit` deployment does not compress either: its NGINX configuration has no `gzip` directive (`docs/adr/0015-the-rest-of-63-is-gzip-in-the-proxy.md`). A page carries the session's CSRF token next to input it reflects, which a compressed response would leak to a BREACH attack if the token were the same in every response; it is masked afresh on every render, so it never is. The server page shows the registration token, so it is left uncompressed, as is the answer that carries a user's API token: the settings page does not print the token, it fetches it when "Show token" is pressed. So are downloads and the `/api` passthrough, which are streamed rather than read into memory, and bodies over 1 MiB. A proxy that compresses on its own leaves already encoded bodies alone; keep `text/html` out of its `gzip_types` all the same, or it compresses the server page. To hand the job to the proxy entirely, set `MCRITWEB_COMPRESS_RESPONSES = False` in `instance/config.py`. Before it gzips a page, MCRITweb drops the indentation and blank lines of the page and the comment lines of its inline scripts, which is about a tenth of a result page gzipped (`docs/adr/0022-pages-are-thinned-before-they-are-gzipped.md`); `MCRITWEB_THIN_PAGES = False` keeps them, to read the page's source as the templates write it.

### Running behind a reverse proxy

If MCRITweb is served through a reverse proxy - which the recommended [docker-mcrit](https://github.com/danielplohmann/docker-mcrit) deployment does, with NGINX in front - the app never sees a client address. Every request arrives from the proxy, so `request.remote_addr` is the proxy's address and the failed-login throttle would meter every caller in the world into a single bucket: ten failed logins from anyone would refuse the next login attempt for *everybody* until the window expired.

Tell MCRITweb how many proxies are in front of it, in `instance/config.py`:

```python
# one reverse proxy (e.g. the NGINX in docker-mcrit) between the internet and this app
TRUSTED_PROXY_COUNT = 1
```

The default is `0`, meaning the app is served directly and nothing in the request headers is trusted. Leave it at `0` unless a proxy really is in front, because `X-Forwarded-For` is written by whoever sent the request until a proxy you trust has appended to it.

The count is a number of hops **from the right-hand end of `X-Forwarded-For`**, because that is the end a trusted proxy appends to (NGINX's `$proxy_add_x_forwarded_for` adds its peer's address after whatever arrived). With `TRUSTED_PROXY_COUNT = 1` and a header reading `1.2.3.4, 5.6.7.8, 203.0.113.7`, the client address is `203.0.113.7` - the entry your proxy wrote - and the two to its left, which the client could have invented, are ignored. The proxy must be configured to append rather than replace; NGINX does this with:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Both ways of getting the number wrong are worth stating plainly:

* **Too low** (say `1` when two proxies are chained) meters the *inner* proxy instead of the client. Every request shares one bucket again, and ten failures from anywhere lock the instance out of login for the length of the window.
* **Too high** (say `2` behind a single proxy) reads past everything your proxy wrote and into what the client sent. An attacker then chooses their own throttle key: a fresh value per request evades the throttle entirely, and a chosen value spends someone else's budget for them. When the header is shorter than the configured count, MCRITweb falls back to the proxy's address - the lockout in the first bullet.

`TRUSTED_PROXY_COUNT` must be a whole number of hops between 0 and 16. Anything else - a bool (`True` is a plausible way to write "yes, I'm behind a proxy", but it does not say how many), a float, a word, a count larger than any real chain - is refused, logged, and treated as 0. It fails closed on purpose: guessing here means either a whole-instance lockout or a throttle the client gets to key.

Only `X-Forwarded-For` and `X-Forwarded-Proto` are honoured, and only when `TRUSTED_PROXY_COUNT` is set. `X-Forwarded-Host`, `-Port` and `-Prefix` are deliberately ignored: they change what the app believes its own address is, nothing here needs them, and a proxy that does not set them would leave them forgeable.

The hop count applies to `X-Forwarded-For` only. `X-Forwarded-Proto` is always read one value deep, because the two headers are written differently: a proxy *appends* to `X-Forwarded-For` (`$proxy_add_x_forwarded_for`, so it grows with the chain) but *replaces* `X-Forwarded-Proto` (`$scheme`, so it carries one value however many proxies there are). If some proxy in your chain appends to `X-Forwarded-Proto` instead of replacing it, MCRITweb reads the innermost value, which is the one you want anyway.

One rough edge, noted rather than fixed: Werkzeug parses `X-Forwarded-For` as a list header, so a client sending something unparseable - an unterminated quote, say - produces no usable address and MCRITweb falls back to the proxy's. Those attempts are then metered against the proxy. It costs the sender their own attempts and nobody else's, since a well-formed request still resolves to its own address, so the fix would be re-parsing the header in front of Werkzeug for no gain.


### HTTPS

Served over HTTPS - directly, or through a proxy that terminates TLS and that `TRUSTED_PROXY_COUNT` counts, so that MCRITweb believes its `X-Forwarded-Proto` - every response carries `Strict-Transport-Security: max-age=31536000; includeSubDomains` and `Cross-Origin-Opener-Policy: same-origin-allow-popups`. Neither is sent over plain HTTP, where a browser ignores HSTS and Chromium reports the COOP header as an error on every page. The HSTS value is the one docker-mcrit's TLS site already adds in NGINX. A browser acts on the first such header in a response, which is MCRITweb's, so set another value here rather than in the proxy, or `None` to send none:

```python
# while trying HTTPS out: a day, to be raised once it works
STRICT_TRANSPORT_SECURITY = "max-age=86400"
```

A browser that has seen the header refuses plain HTTP for the host until its `max-age` has run out, so shorten it well before moving a host off HTTPS.

The session cookie is named `__Host-session`, which a browser accepts only from the host itself, over HTTPS, for the whole site. It falls back to `session` when the cookie cannot meet those terms: with `SESSION_COOKIE_SECURE = False` for plain HTTP, with a `SESSION_COOKIE_DOMAIN`, or with a `SESSION_COOKIE_PATH` or `APPLICATION_ROOT` other than `/`. `SESSION_COOKIE_NAME` in `instance/config.py` overrides either.

### Backend timeout

Every call MCRITweb makes to the MCRIT backend is bounded by `MCRIT_CLIENT_TIMEOUT`, a `(connect, read)` pair in seconds that defaults to `(10, 280)`. Without it a backend that is down or hung held a gunicorn request thread indefinitely, since gunicorn's own timeout does not reclaim a thread under the `gthread` worker. The read bound sits under the 300 s that docker-mcrit's NGINX waits, so a slow backend ends in MCRITweb's error page rather than a proxy 504. Raise both together if long synchronous calls - a whole-corpus import or export through the API passthrough - need more, or set the read to `None` in `instance/config.py` to wait indefinitely. It takes effect with an MCRIT release whose `McritClient` supports a timeout; an older one ignores it.

### SQLite database

MCRITweb keeps its users, their settings and the backend address in `instance/mcritweb.sqlite`. The file runs in SQLite's WAL mode, which MCRITweb switches on at startup and SQLite then keeps in the file, so the two gunicorn workers docker-mcrit starts can read while the other writes. In WAL mode two more files live next to it, `mcritweb.sqlite-wal` and `mcritweb.sqlite-shm`, and recent changes can sit in the `-wal` file until SQLite folds them in. So back the database up through SQLite's backup API, which is safe while MCRITweb runs - the docker-mcrit image has Python but no `sqlite3` shell:

```
python3 -c "import sqlite3; sqlite3.connect('file:instance/mcritweb.sqlite?mode=ro', uri=True).backup(sqlite3.connect('instance/mcritweb-backup.sqlite'))"
```

Run it from the directory that holds `instance/`: opened read-only, a database that is not there is an error rather than a new, empty one to back up, and the backup goes into `instance/`, so under `docker exec` it lands on the bind mount rather than in the container. Or stop MCRITweb and copy all three files together. WAL needs `instance/` on a local filesystem, as docker-mcrit's bind mount is; it does not work on a network share.

### API tokens issued before 1.5.0

Tokens issued before 1.5.0 have the 32-character `md5` form; every token since is 64 hex characters. The 1.5.0 release left the old ones valid - the entropy was always UUID4's, so they were never weak - but nothing moved them on: a 32-character token stayed valid until its owner happened to press regenerate. MCRITweb now tells people about them. A user whose token has the old shape sees a warning next to it on the settings page, with the regenerate button highlighted; an admin sees a count of the accounts still holding one on the users page, a key icon on each such row, and a "Regenerate all legacy tokens" action that rotates all of them at once.

When you are satisfied your users have had the chance to update their scripts, retire the old tokens by setting this in `instance/config.py`:

```python
ACCEPT_LEGACY_APITOKENS = False
```

A request that authenticates with a 32-character token is then answered with a 403 whose message says the token has the pre-1.5.0 form and has to be regenerated - rather than the bare 403 an unknown token gets, so the owner can tell the two apart. The default stays `True` for one release, so nothing changes until an operator chooses the cutover moment.

## Version History

See [CHANGELOG.md](CHANGELOG.md) for the full release history.


## Credits & Notes

MCRITweb uses the following projects:  
* the awesome [CFGExplorer](https://github.com/hdc-arizona/cfgexplorer) library, published by the Humans, Data, and Computers Lab at CS Arizona, is used to visualize disassembly.  
* `bootstrap`, `jquery`, and `font-awesome` for its appearence. 

Pull requests welcome! :)


## License
```
    MCRITweb
    Copyright (C) 2022  Daniel Enders, Manuel Blatt, Daniel Plohmann

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
    
    Some plug-ins and libraries may have different licenses. 
    If so, a license file is provided in the plug-in's folder.
```
