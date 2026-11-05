// The profile's tab row and its snippet copy buttons.
//
// Bootstrap's pills switch the panes on a click. This adds what they leave out: the
// arrow keys, Home and End move between the tabs with one tab stop for the row (a roving
// tabindex), the URL hash names the pane that is shown so a link to #developer opens it,
// and a pane shown by whatever means puts its name in the hash. The panes are switched by
// their classes here, not through bootstrap.Tab, so nothing depends on when Bootstrap's
// script has arrived; a click still goes through Bootstrap, which fires shown.bs.tab.
//
// The script sits at the end of the page, after the tab row, so the elements exist.
(function () {
    var tabList = document.getElementById("profileTabList");
    if (!tabList) {
        return;
    }
    var tabs = Array.prototype.slice.call(tabList.querySelectorAll('button[role="tab"]'));

    function paneOf(tab) {
        return document.querySelector(tab.getAttribute("data-bs-target"));
    }

    function nameOf(tab) {
        return tab.getAttribute("data-bs-target").replace("#pane-", "");
    }

    // the roving tabindex and the selection marks, for whichever tab is shown
    function mark(shown) {
        tabs.forEach(function (tab) {
            var isShown = tab === shown;
            tab.setAttribute("tabindex", isShown ? "0" : "-1");
            tab.setAttribute("aria-selected", isShown ? "true" : "false");
        });
    }

    function show(tab, updateHash) {
        tabs.forEach(function (other) {
            var pane = paneOf(other);
            other.classList.toggle("active", other === tab);
            if (pane) {
                pane.classList.toggle("active", other === tab);
                pane.classList.toggle("show", other === tab);
            }
        });
        mark(tab);
        if (updateHash && window.location.hash !== "#" + nameOf(tab)) {
            history.replaceState(null, "", "#" + nameOf(tab));
        }
    }

    tabList.addEventListener("keydown", function (event) {
        var current = tabs.indexOf(document.activeElement);
        if (current === -1) {
            return;
        }
        var next = -1;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
            next = (current + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
            next = (current - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
            next = 0;
        } else if (event.key === "End") {
            next = tabs.length - 1;
        }
        if (next !== -1) {
            event.preventDefault();
            tabs[next].focus();
            show(tabs[next], true);
        }
    });

    tabs.forEach(function (tab) {
        tab.addEventListener("shown.bs.tab", function () {
            mark(tab);
            if (window.location.hash !== "#" + nameOf(tab)) {
                history.replaceState(null, "", "#" + nameOf(tab));
            }
        });
    });

    function showHash() {
        var name = window.location.hash.replace("#", "");
        var tab = name ? document.getElementById("tab-" + name) : null;
        if (tab && tabs.indexOf(tab) !== -1) {
            show(tab, false);
        }
    }

    showHash();
    window.addEventListener("hashchange", showHash);

    // the example snippets: copies the text as shown, token included once it is shown
    Array.prototype.forEach.call(document.querySelectorAll("[data-copy-snippet]"), function (button) {
        var label = button.textContent;
        button.addEventListener("click", function () {
            var snippet = document.getElementById(button.getAttribute("data-copy-snippet"));
            if (!snippet || !navigator.clipboard) {
                return;
            }
            navigator.clipboard.writeText(snippet.innerText || snippet.textContent).then(function () {
                button.textContent = "Copied";
                setTimeout(function () {
                    button.textContent = label;
                }, 2000);
            });
        });
    });
})();
