/* Feedback while a full page navigation is in flight.
 *
 * Paging through a match report is an ordinary link click: the browser keeps the
 * old page on screen until the new one has arrived, and a result page is ~200 KB
 * of table, so on a busy backend nothing at all happens for several seconds. The
 * spinner is therefore drawn over the *outgoing* page and disappears with it.
 *
 * Only navigations that actually replace this document get one - a middle click,
 * a ctrl/cmd/shift/alt click, target="_blank" or a link that only moves the
 * fragment all leave the page where it is, and a spinner there would never end.
 * Everything therefore goes through isDocumentLoad() before anything is drawn.
 */

var mcritwebPageLoading = (function () {
  "use strict";

  var OVERLAY_ID = "mcritweb-page-loading";
  /* below the threshold where a delay reads as a delay, a spinner is just a flash */
  var SHOW_DELAY_MS = 150;
  /* the DOM has no "navigation aborted" event (stop button, a scheme the browser
   * hands to another application), so bound how long the overlay can survive. Past
   * this the page is no worse off than it was before this file existed. */
  var MAX_VISIBLE_MS = 30000;

  var showTimer = null;
  var hideTimer = null;

  function overlayElement() {
    var element = document.getElementById(OVERLAY_ID);
    if (element !== null) {
      return element;
    }
    element = document.createElement("div");
    element.id = OVERLAY_ID;
    element.setAttribute("role", "status");
    element.setAttribute("aria-live", "polite");
    element.style.cssText = [
      "position:fixed", "top:0", "right:0", "bottom:0", "left:0",
      /* over the Bootstrap modal layer, which ends at 1055 */
      "z-index:2000",
      "display:flex", "align-items:center", "justify-content:center",
      "background:rgba(255,255,255,0.6)"
    ].join(";");
    /* .spinner-border and .visually-hidden are Bootstrap 5; the vendored
     * stylesheet already slows the animation under prefers-reduced-motion */
    element.innerHTML =
      '<div class="spinner-border text-primary" style="width:3rem;height:3rem;"></div>' +
      '<span class="visually-hidden">Loading, please wait.</span>';
    document.body.appendChild(element);
    return element;
  }

  function hide() {
    if (showTimer !== null) {
      window.clearTimeout(showTimer);
      showTimer = null;
    }
    if (hideTimer !== null) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
    var element = document.getElementById(OVERLAY_ID);
    if (element !== null && element.parentNode !== null) {
      element.parentNode.removeChild(element);
    }
  }

  /* Idempotent: a second click while a navigation is already pending must not
   * restart the timers, and once the overlay is up it swallows the click anyway. */
  function show() {
    if (showTimer !== null || document.getElementById(OVERLAY_ID) !== null) {
      return;
    }
    showTimer = window.setTimeout(function () {
      showTimer = null;
      overlayElement();
      hideTimer = window.setTimeout(hide, MAX_VISIBLE_MS);
    }, SHOW_DELAY_MS);
  }

  /* True only if going to `url` replaces this document. Same page plus a different
   * fragment is a scroll, not a load, and the pagination widgets do build such
   * links - every widget renders one for the page you are already on. */
  function isDocumentLoad(url) {
    if (!url) {
      return false;
    }
    var target;
    try {
      target = new URL(url, document.location.href);
    } catch (error) {
      return false;
    }
    if (target.protocol !== "http:" && target.protocol !== "https:") {
      return false;
    }
    var here = document.location;
    return target.origin !== here.origin
      || target.pathname !== here.pathname
      || target.search !== here.search;
  }

  /* For callers that navigate by assigning window.location themselves. */
  function showFor(url) {
    if (isDocumentLoad(url)) {
      show();
    }
  }

  /* The sortable table headers carry their navigation in an inline onclick, in one
   * of two shapes (see the sortable_header_col macro): a direct assignment, whose
   * target has to be read out of the attribute, or a pagination_js_helper() call,
   * which raises the spinner itself. Reading the attribute rather than showing the
   * spinner for any header click keeps the "does this actually load?" test on every
   * path - a header rendered without a pagination gets an empty target, and would
   * otherwise leave a spinner over a page that only reloaded itself. */
  var HEADER_TARGET = /window\.location\.href\s*=\s*(['"])([\s\S]*?)\1/;

  function headerTarget(element) {
    var onclick = element.getAttribute("onclick");
    if (!onclick) {
      return null;
    }
    var match = HEADER_TARGET.exec(onclick);
    return match === null ? null : match[2];
  }

  function isPlainLeftClick(event) {
    return event.button === 0
      && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
  }

  document.addEventListener("click", function (event) {
    /* bubble phase, so an inline onclick that cancelled the navigation is visible */
    if (event.defaultPrevented || !isPlainLeftClick(event)) {
      return;
    }
    var element = event.target;
    if (!element || typeof element.closest !== "function") {
      return;
    }
    var link = element.closest("ul.pagination a.page-link[href]");
    if (link !== null) {
      if (link.hasAttribute("download")) {
        return;
      }
      if (link.target !== "" && link.target !== "_self") {
        return;
      }
      /* .href on the element is already resolved against the document */
      showFor(link.href);
      return;
    }
    /* sortable table headers: .pointer is set only when the header really sorts */
    var header = element.closest("th.pointer[onclick]");
    if (header !== null) {
      showFor(headerTarget(header));
    }
  });

  /* Restored from the back/forward cache with the overlay still painted on it. */
  window.addEventListener("pageshow", function () {
    hide();
  });

  /* The same key that stops a navigation clears what it left behind. */
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" || event.key === "Esc") {
      hide();
    }
  });

  return { show: show, showFor: showFor, hide: hide };
})();
