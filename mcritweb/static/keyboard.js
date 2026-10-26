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
 *
 * Tabs. Bootstrap shows a tab when it is clicked and does nothing more: every tab was a
 * tab stop of its own and the arrow keys did nothing. As in the WAI-ARIA tabs pattern, a
 * tab list is one tab stop now, its selected tab, and Left, Right, Home and End move
 * between its tabs, past a disabled one. A tab is shown as focus reaches it, except in a
 * list marked data-tab-activation="manual", where Enter or Space shows it: the cross
 * compare result builds a matrix the first time its tab is shown, which for a large one
 * takes long enough to be in the way of a key held down. Bootstrap 5.3 does all this
 * itself; 5.0.2 does not.
 *
 * Rows per page. The select of a pager (data-pager-url, written by pagination_widget.html)
 * loads the page at the size it is changed to. A select changes its value at every press of
 * an arrow key, and loading at the first one left a keyboard no way to step through the
 * sizes. So a change made with a key waits for Enter, or for focus to leave the select; one
 * made with the pointer, or a touch screen's picker, loads at once.
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

  function tabsOf(list) {
    return Array.prototype.filter.call(list.querySelectorAll('[role="tab"]'), function (tab) {
      return tab.closest('[role="tablist"]') === list;
    });
  }

  function isUsable(tab) {
    return !tab.disabled && !tab.classList.contains("disabled") && tab.getAttribute("aria-disabled") !== "true";
  }

  function giveTabStop(list, keeper) {
    tabsOf(list).forEach(function (tab) {
      tab.tabIndex = tab === keeper ? 0 : -1;
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[role="tablist"]'), function (list) {
    var tabs = tabsOf(list);
    var selected = tabs.filter(function (tab) {
      return tab.getAttribute("aria-selected") === "true";
    });
    giveTabStop(list, selected[0] || tabs.filter(isUsable)[0]);
  });

  document.addEventListener("shown.bs.tab", function (event) {
    var list = event.target.closest('[role="tablist"]');
    if (list !== null) {
      giveTabStop(list, event.target);
    }
  });

  document.addEventListener("keydown", function (event) {
    var tab = event.target.closest ? event.target.closest('[role="tab"]') : null;
    var list = tab === null ? null : tab.closest('[role="tablist"]');
    if (list === null || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
      return;
    }
    var tabs = tabsOf(list);
    var usable = tabs.filter(isUsable);
    var next = null;
    if (usable.length === 0) {
      return;
    } else if (event.key === "Home") {
      next = usable[0];
    } else if (event.key === "End") {
      next = usable[usable.length - 1];
    } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      var step = event.key === "ArrowRight" ? 1 : tabs.length - 1;
      var at = tabs.indexOf(tab);
      for (var i = 1; i <= tabs.length && next === null; i++) {
        var candidate = tabs[(at + step * i) % tabs.length];
        next = isUsable(candidate) ? candidate : null;
      }
    } else {
      return;
    }
    event.preventDefault();
    next.focus();
    if (list.getAttribute("data-tab-activation") !== "manual") {
      next.click();
    }
  });

  var PAGER = "select[data-pager-url]";
  var changedByKey = new WeakSet();
  var waiting = new WeakSet();

  function loadAtTheChosenSize(select) {
    var changes = {};
    changes[select.dataset.pagerParam] = select.value;
    /* the other tables' state a page carries, from the function its template names */
    var state = select.dataset.pagerState ? window[select.dataset.pagerState]() : {};
    pagination_js_helper(select.dataset.pagerUrl, Object.assign(state, changes));
  }

  function applyTheChoice(select) {
    if (waiting.delete(select)) {
      changedByKey.delete(select);
      loadAtTheChosenSize(select);
    }
  }

  document.addEventListener("keydown", function (event) {
    var select = event.target.closest ? event.target.closest(PAGER) : null;
    if (select === null) {
      return;
    }
    if (event.key === "Enter" && waiting.has(select)) {
      event.preventDefault();
      applyTheChoice(select);
    } else if (event.key !== "Tab" && event.key !== "Shift") {
      changedByKey.add(select);
    }
  });

  document.addEventListener("pointerdown", function (event) {
    var select = event.target.closest ? event.target.closest(PAGER) : null;
    if (select !== null) {
      changedByKey.delete(select);
    }
  });

  document.addEventListener("change", function (event) {
    var select = event.target.closest ? event.target.closest(PAGER) : null;
    if (select !== null) {
      waiting.add(select);
      if (!changedByKey.has(select)) {
        applyTheChoice(select);
      }
    }
  });

  document.addEventListener("focusout", function (event) {
    var select = event.target.closest ? event.target.closest(PAGER) : null;
    if (select !== null) {
      applyTheChoice(select);
    }
  });

  /* A size chosen before this file had loaded was missed. The browser restores a form
   * control's value when it reloads or goes back to a page, with no one choosing, so only a
   * page the browser opened fresh can say a size that is not the markup's was chosen. */
  var opened = performance.getEntriesByType("navigation")[0];
  if (opened && opened.type === "navigate") {
    Array.prototype.some.call(document.querySelectorAll(PAGER), function (select) {
      if (select.selectedOptions.length && !select.selectedOptions[0].defaultSelected) {
        waiting.add(select);
        applyTheChoice(select);
        return true;
      }
      return false;
    });
  }
})();
