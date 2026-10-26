// A short status message at the foot of the window - a copy that worked, or what to do
// when it did not - which a screen reader reads out as it appears. It takes the place of the
// alert dialog, which took the focus and had to be answered before the page could be used
// again, and of the "Copied!" tooltip alone, which a screen reader never announced.
//
//   mcritStatus("Copied to the clipboard.");
//   mcritStatus("... press Ctrl+C to copy it.", {lasting: true});  // until it is closed
//   mcritStatus("... press Ctrl+C to copy it.", {select: text});   // shows text, selected
//
// The element goes on the page empty when this file runs, and stays: a live region is only
// read out reliably when it was on the page before its text changed. Where the browser has
// popovers it is a manual one, opened once, so that it is drawn in the top layer, above a
// dialog and its backdrop; elsewhere it is a fixed box. Either way it is drawn over the page
// and moves nothing on it, and while it is empty it has no size.
// Queued with later() from base.html, so a script reaches it through whenReady(): a click
// can come before the queue has run.

var mcritStatus = (function () {
    var SHOWN = "status-message-shown";
    // base.html puts the empty box on the page, since an element added to <body> or <main>
    // once the page is loaded made the browser restyle all of it (bootstrap's `legend + *`):
    // 300 ms on a 100 by 100 cross compare matrix. Only a page without it makes its own.
    var box = document.querySelector(".status-message");
    if (!box) {
        box = document.createElement("div");
        box.className = "status-message";
        document.body.appendChild(box);
    }
    var text = document.createElement("span");
    text.setAttribute("role", "status");
    // what to copy by hand, outside the live region so that it is shown but not read out
    var value = document.createElement("span");
    value.className = "status-message-value";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.setAttribute("aria-label", "Close the message");
    close.hidden = true;
    box.append(text, value, close);
    if ("popover" in HTMLElement.prototype) {
        box.popover = "manual";
        box.showPopover();
    }

    var timer = null;
    var pending = null;

    function hide() {
        clearTimeout(timer);
        clearTimeout(pending);
        box.classList.remove(SHOWN);
        text.textContent = "";
        value.textContent = "";
        close.hidden = true;
    }
    close.addEventListener("click", hide);

    return function (message, options) {
        options = options || {};
        clearTimeout(timer);
        clearTimeout(pending);
        // emptied, and written a moment later, so that the same message twice is read out twice
        text.textContent = "";
        pending = setTimeout(function () {
            text.textContent = message;
            value.textContent = options.select || "";
            box.classList.add(SHOWN);
            close.hidden = false;
            if (options.select) {
                window.getSelection().selectAllChildren(value);
            }
            if (!options.lasting && !options.select) {
                timer = setTimeout(hide, 5000);
            }
        }, 50);
    };
})();

// What the clipboard buttons do on screen: their Bootstrap tooltips, and the report of a
// copy - the status message above, and "Copied!" in the button's own tooltip. The copy itself
// is copy_to_clipboard() in the page (table/links.html), which a press right after the first
// paint can reach before this file has loaded; it calls mcritCopied() from whenReady() once
// the queue has run. It is here rather than in a file of its own because each file the queue
// adds costs every page on a slow phone: as clipboard.js, these 25 lines added about 40 ms of
// blocking time to the index page in Lighthouse's mobile run.

whenReady(function () {
    $('[data-bs-toggle="tooltip"], [data-toggle="tooltip"]').tooltip();
});

function mcritCopied(btn, value, copied) {
    if (!copied) {
        mcritStatus("Could not copy to the clipboard. It is selected here instead: press Ctrl+C to copy it.", {select: value});
        return;
    }
    mcritStatus("Copied to the clipboard.");
    // a press from before the queue ran reports first: its report was asked for earlier than
    // the set-up above, so the button may not have its tooltip yet
    var tooltip = bootstrap.Tooltip.getInstance(btn) || new bootstrap.Tooltip(btn);
    var title = btn.getAttribute('data-bs-original-title');
    btn.setAttribute('data-bs-original-title', "Copied!");
    tooltip.show();
    btn.setAttribute('data-bs-original-title', title);
}
