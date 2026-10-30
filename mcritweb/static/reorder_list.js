/* Move up and Move down buttons for the lists a pointer orders by dragging: the column
 * settings (SortableJS, settings.html) and a cross compare's sample order (jQuery UI,
 * result_cross.html). Dragging was the only way to order either. No key reached their
 * items, and jQuery UI's drag takes no touch, so a keyboard could set neither the columns
 * of a table nor a custom order of samples, and a phone no custom order.
 *
 * An item is [data-reorder-item], named by its data-reorder-label (or its data-column) in
 * the status messages and by its data-reorder-name, if it has one, in its buttons' names.
 * The script puts the buttons in a .reorder-buttons at the end of each when it runs, after
 * the first paint, into room the stylesheet keeps there: written by the server into every
 * item, they made the settings page, which is sent uncompressed, 58 KB larger. Nothing orders without the script, so
 * nothing is lost without it. The buttons carry data-reorder="up" or "down", and in a list
 * named <table>_active or <table>_available "across", which moves the item to the end of
 * the other list of its table. The item moves, focus stays on the button pressed, and the
 * page's #reorder-status, a polite live region rendered with the page so that it is there
 * before the first message, says where the item went.
 */
(function () {
  "use strict";

  function itemsOf(list) {
    return Array.prototype.filter.call(list.children, function (child) {
      return child.hasAttribute("data-reorder-item");
    });
  }

  /* settings.html names its lists <table>_active and <table>_available */
  function listName(list) {
    if (/_active$/.test(list.id)) {
      return "Active";
    }
    return /_available$/.test(list.id) ? "Available" : "";
  }

  function otherList(list) {
    return Array.prototype.filter.call(list.parentNode.children, function (sibling) {
      return sibling !== list && listName(sibling) !== "";
    })[0] || null;
  }

  /* the button that moves an item across names the list it moves it to, and points there */
  function labelAcross(item) {
    var button = item.querySelector('[data-reorder="across"]');
    var to = button === null ? null : otherList(item.parentNode);
    if (to === null) {
      return;
    }
    var name = listName(to);
    var rightwards = Boolean(item.parentNode.compareDocumentPosition(to) & Node.DOCUMENT_POSITION_FOLLOWING);
    button.setAttribute("aria-label", "Move " + labelOf(item) + " to " + name);
    button.title = "Move to " + name;
    var icon = button.querySelector("i");
    icon.classList.toggle("fa-angle-right", rightwards);
    icon.classList.toggle("fa-angle-left", !rightwards);
  }

  /* a column is named by its data-column, a sample by a data-reorder-label of its own */
  function labelOf(item) {
    return item.getAttribute("data-reorder-label") || item.getAttribute("data-column");
  }

  function makeButton(item, move, icon, title) {
    var button = document.createElement("button");
    var glyph = document.createElement("i");
    var name = item.getAttribute("data-reorder-name") || labelOf(item);
    button.type = "button";
    button.className = "btn btn-outline-dark btn-sm";
    button.setAttribute("data-reorder", move);
    button.setAttribute("aria-label", "Move " + name + " " + move);
    button.title = title;
    glyph.className = "fa-solid " + icon;
    glyph.setAttribute("aria-hidden", "true");
    button.appendChild(glyph);
    return button;
  }

  function addButtons(item) {
    var holder = document.createElement("span");
    holder.className = "reorder-buttons";
    item.appendChild(holder);
    holder.appendChild(makeButton(item, "up", "fa-angle-right fa-rotate-270", "Move up"));
    holder.appendChild(makeButton(item, "down", "fa-angle-right fa-rotate-90", "Move down"));
    if (listName(item.parentNode) !== "") {
      holder.appendChild(makeButton(item, "across", "fa-angle-right", ""));
      labelAcross(item);
    }
  }

  function announce(text) {
    var status = document.getElementById("reorder-status");
    if (status !== null) {
      status.textContent = text;
    }
  }

  function announcePosition(item) {
    var list = item.parentNode;
    var name = listName(list);
    announce(labelOf(item) + " moved to position " + (itemsOf(list).indexOf(item) + 1)
      + " of " + itemsOf(list).length + (name ? " in " + name : "") + ".");
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-reorder]");
    var item = button === null ? null : button.closest("[data-reorder-item]");
    if (item === null) {
      return;
    }
    var list = item.parentNode;
    var items = itemsOf(list);
    var at = items.indexOf(item);
    var label = labelOf(item);
    var move = button.getAttribute("data-reorder");
    if (move === "up") {
      if (at === 0) {
        announce(label + " is already first.");
        return;
      }
      list.insertBefore(item, items[at - 1]);
    } else if (move === "down") {
      if (at === items.length - 1) {
        announce(label + " is already last.");
        return;
      }
      list.insertBefore(item, items[at + 1].nextSibling);
    } else if (move === "across") {
      var to = otherList(list);
      if (to === null) {
        return;
      }
      to.appendChild(item);
      labelAcross(item);
    } else {
      return;
    }
    /* a node moved in the document loses focus on the way */
    button.focus();
    announcePosition(item);
  });

  /* SortableJS fires "add" on the list a drag dropped an item into */
  document.addEventListener("add", function (event) {
    if (event.item && event.item.hasAttribute && event.item.hasAttribute("data-reorder-item")) {
      labelAcross(event.item);
    }
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-reorder-item]"), addButtons);
})();
