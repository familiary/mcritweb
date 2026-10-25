// A data table scrolls sideways in its .table-scroll box below 1200px (style.css), and a
// box that scrolls one way clips the other: the Analyze menu of a sample's row, and the
// actions of a user's, were cut off at the bottom of a short table, with a scrollbar in the
// box to reach the rest. Placed with position: fixed, a menu is clipped by nothing but the
// window. Bootstrap reads that from the dropdown's options, which it takes when it creates
// the dropdown on the first press, so the dropdown is created here first, in the capture
// phase, before Bootstrap's own handler sees the press. Delegated from the document, so
// that the rows a live search swaps in (live_search.js) are covered as well. A press
// before Bootstrap is there is held by base.html and made again once it is.
(function () {
  "use strict";

  function fixedStrategy(config) {
    return Object.assign({}, config, { strategy: "fixed" });
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest && event.target.closest('.table-scroll [data-bs-toggle="dropdown"]');
    if (toggle && window.bootstrap) {
      bootstrap.Dropdown.getOrCreateInstance(toggle, { popperConfig: fixedStrategy });
    }
  }, true);
})();
