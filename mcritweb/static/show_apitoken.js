// The profile's "Show token" button: fetches the account's API token and puts it on the
// page, next to its copy button and into the example snippets.
//
// The page does not print the token: a secret in a page is in every copy of that page,
// in the browser's caches and in view-source, and it would keep the page from being
// compressed once pages are. Only the small answer of admin.show_apitoken carries it. It
// is a POST with the CSRF token, which post_action.js's csrfToken() reads, so no other
// site can make a browser ask for it.
//
// The script sits at the end of the page, after the elements it reads. It needs neither
// jQuery nor Bootstrap.
(function () {
    var button = document.querySelector("[data-show-apitoken]");
    if (!button) {
        return;
    }
    var value = document.getElementById("apitoken-value");
    var copy = document.getElementById("apitoken-copy");
    var copyButton = copy.querySelector(".btn-clipboard");
    var status = document.getElementById("apitoken-status");
    var slots = Array.prototype.slice.call(document.querySelectorAll("[data-apitoken-slot]"));
    var placeholders = slots.map(function (slot) { return slot.textContent; });
    var FAILED = "Could not load the API token. Reload the page and try again; if it fails again, sign in again.";

    // a polite live region, on the page from the start, so that it is read out when it changes
    function say(message, failed) {
        status.textContent = message;
        status.classList.toggle("text-danger", failed);
        // the token itself shows that it worked; only a failure needs to be seen as well
        status.classList.toggle("visually-hidden", !failed);
    }

    function forget() {
        value.textContent = "";
        copyButton.setAttribute("data-clipboard-value", "");
        copy.hidden = true;
        button.hidden = false;
        slots.forEach(function (slot, index) {
            slot.textContent = placeholders[index];
        });
    }

    function show() {
        button.disabled = true;
        fetch(button.getAttribute("data-show-apitoken"), {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: {"X-CSRFToken": csrfToken(), "Accept": "application/json"}
        }).then(function (response) {
            // a session that has ended is sent to /login, and the fetch follows it to a page
            // of HTML: not a token
            var type = response.headers.get("Content-Type") || "";
            if (!response.ok || response.redirected || type.indexOf("application/json") !== 0) {
                throw new Error("no token in the answer");
            }
            return response.json();
        }).then(function (answer) {
            if (!answer || typeof answer.apitoken !== "string" || !answer.apitoken) {
                throw new Error("no token in the answer");
            }
            value.textContent = answer.apitoken;
            copyButton.setAttribute("data-clipboard-value", answer.apitoken);
            slots.forEach(function (slot) {
                slot.textContent = answer.apitoken;
            });
            copy.hidden = false;
            button.hidden = true;
            say("API token shown.", false);
            // the button pressed is gone; the focus goes where the next step is
            copyButton.focus();
        }).catch(function () {
            forget();
            say(FAILED, true);
        }).then(function () {
            button.disabled = false;
        });
    }

    button.addEventListener("click", show);
    // the page may be kept in the back/forward cache, and Back, after logout too, would
    // bring it back with the token on it
    window.addEventListener("pagehide", function () {
        forget();
        status.textContent = "";
    });
})();
