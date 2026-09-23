import math

from flask import Request, url_for


def request_args_for_link_building(request: Request) -> dict:
    """The request's view args and query args, minus the names url_for() reserves.

    Anything that splats request-derived args into url_for() should go through here:
    the two pagination classes, and `explore.search`, which rebuilds its own URL to
    normalise a repeated `?type=`.

    Both pagination classes rebuild the current URL by splatting the incoming args
    back into url_for(), which makes every query parameter a potential collision:
    `endpoint` is url_for()'s own first argument (a `?endpoint=x` raised
    "url_for() got multiple values for argument 'endpoint'" -> HTTP 500), and Flask
    reserves every leading-underscore keyword for itself, so `?_method=DELETE` failed
    the build with a BuildError -> HTTP 500. `?_external=1` and `?_scheme=` are the
    ones with security weight rather than availability weight: they raised no error
    at all and silently rewrote every pagination link into an absolute URL derived
    from the Host header, so a request carrying a spoofed Host handed the visitor a
    page whose paging links pointed at someone else's origin.

    Colliding parameters are dropped rather than preserved in the generated link:
    none of them names a parameter of any route in this app, so there is nothing to
    carry over, and keeping a visitor-supplied `_anchor` alive would only hand the
    same value to the next page. All leading-underscore names go, not just the four
    url_for() reads today, because that whole namespace is Flask's to extend.

    Only request-derived args are filtered. The `kwargs_overwrites` the pagination
    macros pass are not: `table/pagination_widget.html` documents `_anchor` as a
    supported argument and a dozen templates use it, and those come from our own
    markup rather than from the query string.

    Two properties of the merge below are deliberate:

    * It is a `{**a, **b}` merge and not `dict(**a, **b)`, which raises TypeError on
      a duplicate key rather than resolving it. `/data/result/<job_id>?job_id=x` -
      a query parameter shadowing a view arg - was an HTTP 500 for exactly that
      reason, on every paginated route carrying a URL variable.
    * The view args win. They are what the request's path actually resolved to, so
      they are the authoritative value for rebuilding that same path; letting the
      query string win would point every link on `/data/result/<job_id>?job_id=x`
      at a different job than the page the visitor is reading.

    Note that `**request.args` flattens a MultiDict to its first value per key, so a
    repeated query parameter is collapsed in the rebuilt URL. That is long-standing
    behaviour of these call sites and is not changed here.
    """
    args = {**request.args, **(request.view_args or {})}
    return {name: value for name, value in args.items() if name != "endpoint" and not name.startswith("_")}


class Pagination:

    def __init__(self, request: Request, max_value: int, limit=50, query_param="p", limit_param="plimit") -> None:
        self.page = self._getPageFromQueryParam(request, query_param)
        self.limit = self._getLimitFromQueryParam(request, limit_param, limit)
        self.max_value = max_value
        self._pagination_width = 2
        self.page = self.constrained_page

        # used for link generation. 
        self.endpoint = request.endpoint
        self.original_args = request_args_for_link_building(request)
        self.query_param = query_param
        self.limit_param = limit_param

    def _getPageFromQueryParam(self, request, query_param):
        page = 1
        try:
            page = int(request.args.get(query_param))
        except Exception:
            pass
        return max(1, page)

    def _getLimitFromQueryParam(self, request, limit_param, default_limit):
        limit = default_limit
        try:
            requested_limit = int(request.args.get(limit_param))
            # Only allow specific limit values for security
            if requested_limit in [10, 25, 50, 100, 250]:
                limit = requested_limit
        except Exception:
            pass
        return limit

    @property
    def max_page(self):
        return max(1, math.ceil(self.max_value / self.limit))

    @property
    def constrained_page(self):
        return min(self.max_page, self.page)

    @property
    def pages(self):
        start_page = max(1, self.constrained_page - self._pagination_width)
        end_page = min(self.max_page, self.constrained_page + self._pagination_width)

        pages = list(range(start_page, end_page + 1))

        if not pages:
            pages = [1]
        return pages

    @property
    def start_index(self):
        return (self.page - 1) * self.limit

    @property
    def end_index(self):
        return min((self.page - 1) * self.limit + self.limit, self.max_value)

    @property
    def page_index(self):
        pages = self.pages
        if self.constrained_page in pages:
            return pages.index(self.constrained_page)
        else:
            return pages.index(self.max_page)

    def get_link(self, page, **kwargs_overwrites):
        args = {}
        if self.original_args:
            args.update(self.original_args)
        args.update(kwargs_overwrites)
        args[self.query_param] = page
        url = url_for(self.endpoint, **args)
        return url


    def __repr__(self) -> str:
        return f"Pagination(p={self.page}, max_value={self.max_value}, limit={self.limit}) -> constrained_page={self.constrained_page}, start_index={self.start_index}, page_index={self.page_index}, max_page={self.max_page}, pages={self.pages}"
