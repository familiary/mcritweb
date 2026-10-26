// The settings page's "Show token" button: fetches the account's API token and puts it on
// the page, next to its copy button.
//
// The page used to print the token, a secret that is the same in every response, so it
// could not be compressed (a BREACH attack reads such a secret out of a compressed
// response's length) or cached. Only the small answer of admin.show_apitoken carries it
// now. It is a POST with the CSRF token, which base.html's csrfToken() reads, so no other
// site can make a browser ask for it. After a regenerate the page asks once on its own
// (data-show-now), as the new token used to be on the page that followed.
//
// Queued with later() from settings.html. A press before it has run is held by base.html
// and made again once it has. It needs neither jQuery nor Bootstrap.

var mcritShowApitoken = (function () {
    var button = document.querySelector("[data-show-apitoken]");
    if (!button) {
        return null;
    }
    var value = document.getElementById("apitoken-value");
    var copy = document.getElementById("apitoken-copy");
    var copyButton = copy.querySelector(".btn-clipboard");
    var status = document.getElementById("apitoken-status");
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
    }

    function show(pressed) {
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
            copy.hidden = false;
            button.hidden = true;
            say("API token shown.", false);
            // the button pressed is gone; the focus goes where the next step is
            if (pressed) {
                copyButton.focus();
            }
        }).catch(function () {
            forget();
            say(FAILED, true);
        }).then(function () {
            button.disabled = false;
        });
    }

    button.addEventListener("click", function () {
        show(true);
    });
    // the page may be kept in the back/forward cache, and Back, after logout too, would
    // bring it back with the token on it
    window.addEventListener("pagehide", function () {
        forget();
        status.textContent = "";
    });
    if (button.hasAttribute("data-show-now")) {
        show(false);
    }
    return show;
})();
