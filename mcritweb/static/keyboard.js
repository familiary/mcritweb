/* Keyboard behaviour Bootstrap 5.0.2 leaves out, for every page. base.html queues this
 * file with later(), so it runs once the page has been parsed; it only sets tab stops
 * and listens, and reaches Bootstrap through the events Bootstrap fires.
 *
 * Dialogs. Bootstrap focuses a dialog's own box when it opens, so the `autofocus` field
 * of the compare and rename dialogs never got the cursor. On close it hands focus back
 * only to a data-bs-toggle opener that is still visible: the jobs page's delete dialog,
 * which a script opens, and a dialog opened from a menu entry, which the menu has hidden
 * again by then, left focus on <body>, and a keyboard user started over at the top of
 * the page.
 */
(function () {
  "use strict";

  var openers = new WeakMap();

  function isShown(element) {
    return element.isConnected && element.getClientRects().length > 0;
  }

  document.addEventListener("show.bs.modal", function (event) {
    var dialog = event.target;
    var opener = event.relatedTarget || document.activeElement;
    if (opener && opener !== document.body && !dialog.contains(opener)) {
      openers.set(dialog, opener);
    }
  });

  document.addEventListener("shown.bs.modal", function (event) {
    var field = event.target.querySelector("[autofocus]");
    if (field !== null) {
      field.focus();
    }
  });

  document.addEventListener("hidden.bs.modal", function (event) {
    var dialog = event.target;
    var opener = openers.get(dialog);
    openers.delete(dialog);
    var active = document.activeElement;
    /* Bootstrap, or the page, has put focus somewhere already */
    if (!opener || (active && active !== document.body && !dialog.contains(active))) {
      return;
    }
    if (!isShown(opener)) {
      /* a menu entry: its menu has closed, so its toggle is where the menu was */
      var menu = opener.closest(".dropdown-menu");
      opener = menu === null ? null : menu.parentNode.querySelector('[data-bs-toggle="dropdown"]');
    }
    if (opener && isShown(opener)) {
      opener.focus();
    }
  });
})();
