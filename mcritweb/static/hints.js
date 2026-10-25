// Escape for the hint.css tooltips (the data-hint attribute). A tooltip covers what is
// beside it, and hint.css shows it for as long as the pointer or the keyboard focus stays
// on its element, with no way to put it away short of moving off (WCAG 1.4.13). Escape
// hides the one that is showing; it stays hidden until the pointer or the focus reaches
// another element. style.css hides every tooltip while <html> carries the class.
// Queued with later() from base.html: only listens for events.

(function () {
    var DISMISSED = "hints-dismissed";
    // the elements whose tooltips were showing when Escape was pressed
    var dismissed = [];

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") {
            return;
        }
        var shown = document.querySelectorAll("[data-hint]:hover, [data-hint]:focus-visible");
        if (shown.length) {
            dismissed = Array.prototype.slice.call(shown);
            document.documentElement.classList.add(DISMISSED);
        }
    });

    function wake(event) {
        if (!dismissed.length) {
            return;
        }
        var hint = event.target.closest ? event.target.closest("[data-hint]") : null;
        if (dismissed.indexOf(hint) < 0) {
            dismissed = [];
            document.documentElement.classList.remove(DISMISSED);
        }
    }
    document.addEventListener("mouseover", wake);
    document.addEventListener("focusin", wake);
})();
