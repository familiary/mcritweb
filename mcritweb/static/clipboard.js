// What the clipboard buttons do on screen: their Bootstrap tooltips, and the report of a
// copy - a status message for a screen reader, and "Copied!" in the button's own tooltip.
// The copy itself is copy_to_clipboard() in the page (table/links.html), which a press
// right after the first paint can reach before this file has loaded; it calls
// mcritCopied() from whenReady() once the queue has run.
// Queued with later() from base.html, after status_message.js and before Bootstrap.

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
