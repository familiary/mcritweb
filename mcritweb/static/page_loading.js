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
  var STATUS_ID = "mcritweb-page-loading-status";
  /* below the threshold where a delay reads as a delay, a spinner is just a flash */
  var SHOW_DELAY_MS = 150;
  /* the DOM has no "navigation aborted" event (stop button, a scheme the browser
   * hands to another application), so bound how long the overlay can survive. Past
   * this the page is no worse off than it was before this file existed. */
  var MAX_VISIBLE_MS = 30000;

  var showTimer = null;
  var hideTimer = null;

  /* A screen reader reads out a live region when its text changes, and only one it
   * already knew of: a region made with its text in it, as the overlay was, is often
   * not read at all. So the region is on the page empty, long before a click can need
   * it, and the overlay itself is only the picture. base.html carries it, since an
   * element added to <body> once the page has loaded makes the browser restyle all of
   * it (bootstrap's `legend + *`); only a page without it makes its own. */
  function statusElement() {
    var element = document.getElementById(STATUS_ID);
    if (element === null) {
      element = document.createElement("div");
      element.id = STATUS_ID;
      element.className = "visually-hidden";
      element.setAttribute("role", "status");
      document.body.appendChild(element);
    }
    return element;
  }

  function overlayElement() {
    var element = document.getElementById(OVERLAY_ID);
    if (element !== null) {
      return element;
    }
    element = document.createElement("div");
    element.id = OVERLAY_ID;
    element.style.cssText = [
      "position:fixed", "top:0", "right:0", "bottom:0", "left:0",
      /* over the Bootstrap modal layer, which ends at 1055 */
      "z-index:2000",
      "display:flex", "align-items:center", "justify-content:center",
      /* the page ground at 60%, from the palette in style.css (#70) */
      "background:var(--surface-veil)"
    ].join(";");
    /* .spinner-border is Bootstrap 5; the vendored stylesheet already slows the
     * animation under prefers-reduced-motion */
    element.innerHTML =
      '<div class="spinner-border text-primary" style="width:3rem;height:3rem;"></div>';
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
    statusElement().textContent = "";
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
      statusElement().textContent = "Loading, please wait.";
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
    /* a pager link, or the link in a sortable table header (sortable_header_col). One
     * whose onclick calls pagination_js_helper() has cancelled the click above, and
     * the helper raises the spinner itself */
    var link = element.closest("ul.pagination a.page-link[href], th > a.table-sort[href]");
    if (link !== null) {
      if (link.hasAttribute("download")) {
        return;
      }
      if (link.target !== "" && link.target !== "_self") {
        return;
      }
      /* .href on the element is already resolved against the document */
      showFor(link.href);
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

  statusElement();

  return { show: show, showFor: showFor, hide: hide };
})();
