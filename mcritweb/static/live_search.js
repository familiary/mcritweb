// A search form marked `data-live-search="<id>"` filters as its term is typed: the page the
// form would load is fetched, and its element of that id (the table and its pagination)
// is swapped in, with the messages flashed along with it. Enter still loads the whole
// page, as it does without script. Used where a search is cheap enough to run on every
// pause in typing; docs/adr/0016-slow-function-search-is-mcrits.md is about the searches
// that are not.
(function () {
  "use strict";

  // long enough that typing a name or an offset sends one request, not one per key
  var TYPING_PAUSE_MS = 300;
  // a new term starts from the first page, whatever its pagination's prefix
  var PAGE_POSITION = /(^|_)(cursor|page)$/;
  var PAGE_SIZE = /(^|_)limit$/;

  function urlFor(term) {
    var url = new URL(window.location.href);
    var keys = Array.from(url.searchParams.keys());
    keys.forEach(function (key) {
      if (PAGE_POSITION.test(key)) {
        url.searchParams.delete(key);
      }
    });
    keys.forEach(function (key) {
      // the pagination reads a page size only along with a page number
      if (PAGE_SIZE.test(key)) {
        url.searchParams.set(key.replace(PAGE_SIZE, "$1page"), "1");
      }
    });
    url.searchParams.set("query", term);
    url.hash = "";
    return url;
  }

  // the page's flashed messages in place of the ones shown; true when one is an error
  function showMessages(page) {
    var content = document.querySelector("section.content");
    var fresh = page.querySelector("section.content");
    if (!content || !fresh) {
      return false;
    }
    content.querySelectorAll(":scope > .alert").forEach(function (alert) { alert.remove(); });
    var first = content.firstChild;
    var alerts = fresh.querySelectorAll(":scope > .alert");
    alerts.forEach(function (alert) {
      content.insertBefore(document.importNode(alert, true), first);
    });
    return Array.prototype.some.call(alerts, function (alert) { return alert.classList.contains("alert-danger"); });
  }

  // The fresh content, ready to go in. A script in it runs, as it would have on a page
  // load (one parsed by DOMParser never does): the edit modals carry their own.
  function adopt(fresh) {
    return Array.prototype.map.call(fresh.childNodes, function (node) {
      if (node.nodeName === "SCRIPT") {
        var script = document.createElement("script");
        script.textContent = node.textContent;
        return script;
      }
      return document.importNode(node, true);
    });
  }

  function liveSearch(form) {
    var region = document.getElementById(form.dataset.liveSearch);
    var input = form.querySelector("input[name=query]");
    var status = document.getElementById(form.dataset.liveSearch + "-status");
    if (!region || !input) {
      return;
    }
    // the term the page was rendered for: base.html's queue runs this after the first
    // paint (ADR-0021), and the box has focus from the start, so it may hold more by now
    var shownTerm = input.defaultValue;
    var timer = null;
    var inFlight = null;

    function search() {
      var term = input.value;
      // whatever is on its way is for an older term, even when the term is back to the
      // one on screen
      if (inFlight) {
        inFlight.abort();
        inFlight = null;
        region.removeAttribute("aria-busy");
      }
      if (term === shownTerm) {
        return;
      }
      var controller = new AbortController();
      inFlight = controller;
      var url = urlFor(term);
      region.setAttribute("aria-busy", "true");
      fetch(url, { signal: controller.signal, credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.text();
        })
        .then(function (html) {
          var page = new DOMParser().parseFromString(html, "text/html");
          var fresh = page.getElementById(region.id);
          if (!fresh) {
            // not this page: the login page once the session has expired, or a redirect
            throw new Error("the answer has no #" + region.id);
          }
          if (document.querySelector(".modal.show")) {
            // an edit modal opened meanwhile lives in the region; swapping it out would
            // leave its backdrop over a page nobody can use. Search again once it is closed.
            document.addEventListener("hidden.bs.modal", search, { once: true });
            return;
          }
          region.replaceChildren.apply(region, adopt(fresh));
          var failed = showMessages(page);
          shownTerm = term;
          window.history.replaceState(null, "", url);
          if (status) {
            var rows = region.querySelectorAll("tbody > tr").length;
            status.textContent = failed ? "The search failed, see the message above"
              : rows ? rows + (rows === 1 ? " row" : " rows") + " on this page" : "No results";
          }
        })
        .catch(function (error) {
          if (error.name === "AbortError") {
            return;
          }
          // whatever went wrong, the page the form would load says it best
          window.location.assign(url);
        })
        .finally(function () {
          if (inFlight === controller) {
            inFlight = null;
            region.removeAttribute("aria-busy");
          }
        });
    }

    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, TYPING_PAUSE_MS);
    });
    // Enter loads the whole page; a pending search would only race it
    form.addEventListener("submit", function () {
      window.clearTimeout(timer);
      if (inFlight) {
        inFlight.abort();
      }
    });
    // what was typed before this ran is searched as if typed now
    if (input.value !== shownTerm) {
      timer = window.setTimeout(search, TYPING_PAUSE_MS);
    }
  }

  document.querySelectorAll("form[data-live-search]").forEach(liveSearch);
})();
