# MCRITweb
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/fkie-cad/mcritweb)

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


### Backend timeout

Every call MCRITweb makes to the MCRIT backend is bounded by `MCRIT_CLIENT_TIMEOUT`, a `(connect, read)` pair in seconds that defaults to `(10, 280)`. Without it a backend that is down or hung held a gunicorn request thread indefinitely, since gunicorn's own timeout does not reclaim a thread under the `gthread` worker. The read bound sits under the 300 s that docker-mcrit's NGINX waits, so a slow backend ends in MCRITweb's error page rather than a proxy 504. Raise both together if long synchronous calls - a whole-corpus import or export through the API passthrough - need more, or set the read to `None` in `instance/config.py` to wait indefinitely. It takes effect with an MCRIT release whose `McritClient` supports a timeout; an older one ignores it.

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
