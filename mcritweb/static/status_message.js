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
    var box = document.createElement("div");
    box.className = "status-message";
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
    document.body.appendChild(box);
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
