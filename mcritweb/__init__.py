import datetime
import os

from flask import Flask, g, redirect, render_template, request, send_from_directory, url_for

#: Ceiling on TRUSTED_PROXY_COUNT. A CDN in front of a load balancer in front of NGINX
#: is three hops; nothing real is anywhere near this. The point is that a fat-fingered
#: count is refused loudly instead of installed: ProxyFix accepts x_for=1000000000
#: happily and then never matches a header, so every request falls back to the proxy's
#: address - the whole-instance login lockout this setting exists to prevent, restored
#: with nothing in the log to point at it.
MAX_TRUSTED_PROXY_COUNT = 16


def _trusted_proxy_count(configured, logger):
    """Validate TRUSTED_PROXY_COUNT, or 0 - trust nothing - if it is not a hop count.

    Every rejection is a fall back to 0 and a warning, because the alternative is to
    guess: the value decides whose address the /login throttle meters, and a wrong guess
    is either a whole-instance lockout or a throttle an attacker can key themselves.

    `int()` alone is not this check. It accepts a bool - and `TRUSTED_PROXY_COUNT = True`
    is the plausible typo, an operator answering "yes, I am behind a proxy" without
    saying how many - landing on `1`, the blind one-hop default this setting exists to
    avoid. It also truncates a float, turning 1.9 into a confident 1. A string of digits
    is the one lenient case, because environment-driven config arrives as text and "2" is
    not ambiguous about anything.
    """
    if isinstance(configured, bool):
        value = None
    elif isinstance(configured, int):
        value = configured
    elif isinstance(configured, str):
        try:
            value = int(configured.strip())
        except ValueError:
            value = None
    else:
        value = None
    if value is None or not 0 <= value <= MAX_TRUSTED_PROXY_COUNT:
        logger.warning(
            "TRUSTED_PROXY_COUNT=%r is not a proxy hop count between 0 and %d; "
            "trusting no proxy, so request addresses will be the proxy's",
            configured, MAX_TRUSTED_PROXY_COUNT)
        return 0
    return value


def create_app(test_config=None, instance_path=None):
    # NOTE: these are imported here rather than at module scope on purpose. Importing any
    # mcritweb submodule executes this file first, so module-level blueprint imports would
    # drag the entire application stack (mcrit, smda, numpy, PIL, networkx) into every
    # import - including a test that only wants a pure-python helper. See issue #88.
    from flask_dropzone import Dropzone
    from mcrit.storage.SampleEntry import SampleEntry

    from . import db, manual
    from .csrf import CsrfProtect
    from .secret_key import INSECURE_DEFAULT, load_or_create_secret_key
    from .views import administration, analyze, api, authentication, data, explore
    from .views.client import get_client
    from .views.params import get_minhash_matching_label
    from .views.utility import ensure_local_data_paths, get_mcritweb_version_from_setup

    # create and configure the app
    # instance_path is overridable so tests get their own cache/temp/uploads tree
    # instead of writing into the deployment's instance folder
    app = Flask(__name__, instance_relative_config=True, instance_path=instance_path)
    app.config.from_mapping(
        SECRET_KEY=INSECURE_DEFAULT,
        DATABASE=os.path.join(app.instance_path, 'mcritweb.sqlite'),
        # the session cookie is not needed by any script we ship, and a cross-site
        # navigation has no business carrying it. Defence in depth behind the CSRF
        # token, not a replacement for it - "Lax" still permits top-level GET.
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_HTTPONLY=True,
        # the session cookie is the entire proof of identity, and the reference
        # deployment (docker-mcrit) terminates TLS in NGINX in front of the app, so it
        # should never travel in the clear. Not while debugging, though: `flask_env.sh`
        # sets FLASK_DEBUG=1 for a local run over plain HTTP, where a secure-only cookie
        # is never sent back and login fails with nothing to point at. Set it explicitly
        # in instance/config.py to override either way.
        SESSION_COOKIE_SECURE=not app.debug,
        # Werkzeug rejects a larger body with 413 before buffering it. This is a ceiling
        # on absurdity rather than a tuned limit: it applies to every route uniformly,
        # and /data/import takes whole-corpus exports, so it has to clear those. The
        # per-role cap below is the fine-grained control.
        MAX_CONTENT_LENGTH=1024 * 2**20,
        # Per-role ceiling on a query upload, in bytes. A role absent from the mapping is
        # uncapped beyond MAX_CONTENT_LENGTH. Issue #19: this was hardcoded at 1 MiB for
        # visitors, which is the right default but the wrong place for it.
        QUERY_UPLOAD_LIMITS={'visitor': 1 * 2**20},
        # How many reverse proxies sit in front of this app, all of which append to
        # X-Forwarded-For. 0 means "served directly": nothing about the request is
        # taken from a header. See the block below create_app's config load.
        TRUSTED_PROXY_COUNT=0,
        # (connect, read) seconds every MCRIT client call may take. requests waits forever
        # by default, so a backend that is down or hung held a gunicorn thread for good:
        # under the gthread worker docker-mcrit runs, gunicorn's -t does not reclaim it.
        # 280 s stays under the 300 s docker-mcrit's NGINX waits, so a slow backend ends in
        # MCRITweb's own error page rather than a 504 from the proxy. None waits forever.
        # Honoured by mcrit releases whose McritClient has a `timeout`; older ones ignore it.
        MCRIT_CLIENT_TIMEOUT=(10, 280),
    )

    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)

    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)

    # Behind a reverse proxy - and the recommended deployment, docker-mcrit, terminates
    # TLS in NGINX in front of this app - the only peer the WSGI server ever sees is the
    # proxy, so `request.remote_addr` is the proxy's address on every request. Anything
    # keyed on it then meters the whole internet into one bucket: the /login throttle
    # (#101) would refuse *everybody* for the length of its window after ten failures
    # from anyone, and would meter nothing per attacker, which is the protection it
    # exists to provide. ProxyFix rewrites REMOTE_ADDR from X-Forwarded-For.
    #
    # The count is the operator's to give, and the default is 0 - trust nothing.
    # X-Forwarded-For is client-supplied until a proxy we trust has appended to it, so
    # trusting it uninvited is how a working throttle becomes a no-op: an attacker sends
    # a fresh value per request and every request is a new bucket, or picks a victim's
    # address and spends their budget for them. A directly served instance must not be
    # made worse by this setting existing.
    #
    # N counts hops from the RIGHT: ProxyFix takes the Nth value from the end of the
    # header, which is the only part of it a trusted proxy wrote (NGINX's
    # `proxy_add_x_forwarded_for` appends), and ignores whatever the client put to its
    # left. Both ways of getting N wrong are documented in the README: too low meters a
    # proxy again, too high hands the key to the client.
    #
    # A value that is not a hop count falls back to 0 rather than to ProxyFix's own
    # default, which trusts one hop: a typo in instance/config.py must fail closed.
    trusted_proxy_count = _trusted_proxy_count(
        app.config.get('TRUSTED_PROXY_COUNT', 0), app.logger)
    app.config['TRUSTED_PROXY_COUNT'] = trusted_proxy_count
    if trusted_proxy_count:
        from werkzeug.middleware.proxy_fix import ProxyFix
        # ProxyFix defaults x_for and x_proto to 1, so every count is passed explicitly.
        #
        # x_proto is 1 at every hop count, and that is not an oversight. The two headers
        # are written differently: a proxy *appends* to X-Forwarded-For
        # (`$proxy_add_x_forwarded_for`), so its depth grows with the chain, but it
        # *replaces* X-Forwarded-Proto (`$scheme`), so the header carries exactly one
        # value - the innermost trusted proxy's - however many proxies there are.
        # Asking for the Nth value of a one-value header gets None, and ProxyFix then
        # leaves the scheme alone: at TRUSTED_PROXY_COUNT = 2 behind TLS every
        # `url_for(..., _external=True)` would silently come out http://, including the
        # registration link admin_server.html hands the admin to send to people.
        #
        # -Host, -Port and -Prefix change what the app believes its own address is,
        # which nothing here needs and which a proxy that does not set them would leave
        # forgeable, so they stay off.
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_proxy_count,
            x_proto=1,
            x_host=0,
            x_port=0,
            x_prefix=0,
        )

    # To enable profiling, put "PROFILER=True" in your config.py (stored in instance folder)
    profiling = False
    try:
        profiling = app.config.get("PROFILER")
    except KeyError:
        pass

    if app.debug and profiling:
        from werkzeug.middleware.profiler import ProfilerMiddleware
        app.config["PROFILE"] = True
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "instance", "profiler")
        os.makedirs(profile_dir, exist_ok=True)
        app.wsgi_app = ProfilerMiddleware(
            app.wsgi_app,
            restrictions=[30],
            profile_dir=profile_dir,
            filename_format="{method}-{path}-{time:.0f}-{elapsed:.0f}ms.prof",
        )

    # ensure the instance and cache folders exists
    ensure_local_data_paths(app)

    # after the config has loaded, so an explicit key in instance/config.py wins
    if app.config['SECRET_KEY'] == INSECURE_DEFAULT:
        app.config['SECRET_KEY'] = load_or_create_secret_key(app.instance_path)

    csrf = CsrfProtect(app)
    # authenticates by `apitoken` header, never by the session cookie, so a
    # cross-site request has nothing to ride on. See mcritweb/csrf.py.
    csrf.exempt(api.bp)

    db.init_app(app)
    db.migrate(app)
    app.register_blueprint(explore.bp)
    app.register_blueprint(analyze.bp)
    app.register_blueprint(authentication.bp)
    app.register_blueprint(administration.bp)
    app.register_blueprint(data.bp)
    app.register_blueprint(api.bp)
    app.config['MCRITWEB_VERSION'] = get_mcritweb_version_from_setup()
    app.config['DROPZONE_DEFAULT_MESSAGE'] = "Drop file or click here to import"
    app.config['DROPZONE_ALLOWED_FILE_CUSTOM'] = True
    app.config['DROPZONE_ALLOWED_FILE_TYPE'] = ""
    # sends the token as an X-CSRF-Token header on every upload; it reads the token
    # through app.extensions["csrf"], which CsrfProtect registered above
    app.config['DROPZONE_ENABLE_CSRF'] = True
    Dropzone(app)

    @app.template_filter('silent')
    def silent(input):
        return ""

    @app.template_filter('capitalize_all')
    def capitalize_all(input):
        return " ".join(map(str.capitalize, input.split(" ")))

    @app.template_filter('getattr')
    def _getattr(obj, attr, default):
        return getattr(obj, attr, default)

    @app.template_filter('date')
    def date(input):
        if isinstance(input, datetime.datetime):
            return input.strftime("%Y-%m-%d")
        elif isinstance(input, str):
            return input[:10]

    @app.template_filter('time')
    def time(input):
        return input[11:19]

    @app.template_filter('date_time')
    def date_time(input):
        return input[:10] + ' ' + input[11:19]
    

    @app.template_global()
    def join_hint_strings(list_of_strings):
        return "\n".join(sorted(list_of_strings))

    # a global rather than a template variable: the job table macro is reached from a
    # dozen templates, all of which would otherwise have to pass this through
    @app.template_global()
    def minhash_matching_label(job_info):
        return get_minhash_matching_label(job_info)

    # the user manual. Public, and deliberately not under /admin: it was the only
    # route in that blueprint without an admin gate, which made the prefix a lie.
    # Rendered from docs/manual/README.md, which is the only copy - see issue #91.
    @app.route('/help')
    def help():
        return render_template('help.html', manual=manual.render(url_for('help_image', filename='')))

    # the manual's screenshots stay next to the markdown so its relative links work
    # for readers on GitHub, which is that copy's whole purpose
    @app.route('/help/images/<path:filename>')
    def help_image(filename):
        return send_from_directory(manual.IMAGE_DIRECTORY, filename)

    @app.route('/', methods=('GET', 'POST'))
    def index():
        if db.is_first_user():
            return redirect(url_for("authentication.register"))
        if request.method == 'POST':
            return redirect(url_for("explore.search", query=request.form["Search"]))
        elif g.user is None or g.user.role == 'pending':
            # index.html shows these callers nothing, so querying the backend for
            # them only spends its time. A role decorator cannot do this job: index
            # is also the first-user entry point and has to reach the redirect above.
            return render_template("index.html", samples={}, families={}, latest_samples=[], jobs=[])
        else:
            client = get_client()
            jobs = client.getQueueData(0, 5, method="getMatchesForSample", state="finished", ascending=False)
            samples_by_id = {}
            families_by_id = {}
            latest_samples = []
            if jobs:
                for job in jobs:
                    if job.sample_ids is not None:
                        for sample_id in [sid for sid in job.sample_ids if sid not in samples_by_id]:
                            samples_by_id[sample_id] = client.getSampleById(sample_id)
                for job in jobs:
                    if job.family_id is not None:
                        families_by_id[job.family_id] = client.getFamily(job.family_id)
            
            sample_results = client.search_samples("", is_ascending=False, cursor=None, sort_by="sample_id", limit=5)
            if sample_results:
                for sample_dict in sample_results['search_results'].values():
                    latest_samples.append(SampleEntry.fromDict(sample_dict))
            return render_template("index.html", samples=samples_by_id, families=families_by_id, latest_samples=latest_samples, jobs=jobs)

    return app