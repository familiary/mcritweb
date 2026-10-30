// Find in page reaches into the closed panels of an accordion. A closed Bootstrap panel is
// display: none, which the browser's search skips: a PicBlockHash or a MinHash looked for
// with Ctrl+F on a function's page was not found until its panel had been opened. A panel
// marked hidden="until-found" keeps its text searchable while it looks closed, and the
// browser fires beforematch on it when a search lands inside, which opens it the way its
// button does.
//
// Bootstrap's classes stay what says whether a panel is open; the attribute follows them,
// put on once a panel has closed and taken off before one opens. A browser without
// until-found (Safari) gets nothing from here, so its panels behave as they always did,
// and so does every browser until this has run. style.css undoes the display: none that
// Bootstrap gives a closed panel and a [hidden] element, for marked panels only.
whenReady(function () {
  "use strict";

  if (!("onbeforematch" in HTMLElement.prototype)) {
    return;
  }
  var PANEL = ".accordion-collapse";

  document.querySelectorAll(PANEL + ".collapse:not(.show)").forEach(function (panel) {
    panel.setAttribute("hidden", "until-found");
  });

  document.addEventListener("beforematch", function (event) {
    var panel = event.target;
    if (panel.matches && panel.matches(PANEL)) {
      // Bootstrap 5.0 has no getOrCreateInstance; a panel never opened has no instance yet
      (bootstrap.Collapse.getInstance(panel) || new bootstrap.Collapse(panel, {toggle: false})).show();
    }
  });
  // before Bootstrap measures the panel to slide it open, which it cannot while its
  // content is hidden
  document.addEventListener("show.bs.collapse", function (event) {
    if (event.target.matches(PANEL)) {
      event.target.removeAttribute("hidden");
    }
  });
  document.addEventListener("hidden.bs.collapse", function (event) {
    if (event.target.matches(PANEL)) {
      event.target.setAttribute("hidden", "until-found");
    }
  });
});
