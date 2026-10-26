// The tooltip of an icon-only link or button: its name, which its aria-label already says.
// A title with the same words as the aria-label beside it gave a table of a hundred rows
// the text twice per row, in every page that sent it. Only a mouse reads a title (the
// aria-label names the control for everything else), so it is set here once the page is
// painted, and the markup carries the name once. A title the markup does set, and the
// Bootstrap tooltips that take theirs over (data-bs-toggle="tooltip"), are left alone.
// Queued with later() from base.html: it changes an attribute, which moves nothing.

(function () {
    var ICON_ONLY = 'a[aria-label]:not([title]):not([data-bs-toggle="tooltip"]), '
        + 'button[aria-label]:not([title]):not([data-bs-toggle="tooltip"])';

    function titleIt(control) {
        if (control.textContent.trim() === "") {
            control.title = control.getAttribute("aria-label");
        }
    }

    document.querySelectorAll(ICON_ONLY).forEach(titleIt);
    // what a script adds after this one has run (the rows a search or a lazy pane brings
    // in) gets its title when the pointer first reaches it
    document.addEventListener("mouseover", function (event) {
        var control = event.target.closest ? event.target.closest(ICON_ONLY) : null;
        if (control) {
            titleIt(control);
        }
    });
})();
