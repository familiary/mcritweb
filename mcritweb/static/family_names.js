// The family type-ahead on the edit modals' and the upload form's family fields. The names
// are fetched as they are typed rather than shipped with the page: mcrit answers "all
// families" with one storage lookup per family, so every listing that carries an edit
// modal used to pay for the whole family table whether or not anyone opened one. See
// issue #77. templates/js/ac_family_names.html queues this file and names the endpoint
// (`mcritFamilyNamesUrl`); a listing includes it twice, once for its edit modals and once
// for the drop overlay's form (#192), and later() queues the file once.
// Autocomplete builds a Bootstrap dropdown, and Bootstrap loads after the page is
// painted (issue #63, ADR-0021): construct them once it has run, from whenReady. The
// callback is also the scope that keeps these names off `window`.
whenReady(function () {
    const familyNamesUrl = window.mcritFamilyNamesUrl;

    function attachFamilyAutocomplete(field) {
        if (!field || field.dataset.familyAutocomplete) {
            return;
        }
        field.dataset.familyAutocomplete = "attached";
        let pending = null;
        let debounce = null;
        const completer = new Autocomplete(field, {
            data: [],
            maximumItems: 5,
            threshold: 1,
            onInput: function (typed) {
                window.clearTimeout(debounce);
                if (typed.length < 1) {
                    // the widget's own threshold - it renders nothing below it, so
                    // clearing the field must not cost a round trip
                    return;
                }
                debounce = window.setTimeout(function () {
                    if (pending) {
                        // a keystroke has overtaken the answer to the last one
                        pending.abort();
                    }
                    const request = new AbortController();
                    pending = request;
                    fetch(familyNamesUrl + '?q=' + encodeURIComponent(typed), {signal: request.signal})
                        .then(function (response) { return response.ok ? response.json() : {}; })
                        .then(function (data) {
                            if (request !== pending) {
                                return;
                            }
                            // already {label, value}, and already escaped: the
                            // endpoint builds them with mcritweb.autocomplete,
                            // because this widget renders a suggestion through
                            // innerHTML. See #168.
                            completer.setData(data.suggestions || []);
                            announce();
                        })
                        // an aborted request, an expired session answering with the
                        // login page, an unreachable backend: no suggestions, and the
                        // field stays a plain text input
                        .catch(function () {});
                }, 150);
            },
        });
        // the menu the widget inserted right after the field
        const announce = describeAsCombobox(field, field.nextElementSibling);
    }

    // The widget (vendored, not ours to change) builds a Bootstrap dropdown of buttons and
    // tells a screen reader nothing about it. The field becomes a combobox that controls a
    // listbox of the suggestions, and how many came for what was typed is said in a polite
    // status beside the field: inside the same dialog, since a modal's outside may be out of
    // a screen reader's reach.
    function describeAsCombobox(field, menu) {
        menu.id = field.id + "-suggestions";
        menu.setAttribute("role", "listbox");
        menu.setAttribute("aria-label", "Family suggestions");
        field.setAttribute("role", "combobox");
        field.setAttribute("aria-autocomplete", "list");
        field.setAttribute("aria-controls", menu.id);
        field.setAttribute("aria-expanded", "false");
        // the widget builds the suggestions afresh on every keystroke
        new MutationObserver(function () {
            menu.querySelectorAll(".dropdown-item").forEach(function (item) {
                item.setAttribute("role", "option");
            });
        }).observe(menu, {childList: true});
        const status = document.createElement("div");
        status.className = "visually-hidden";
        status.setAttribute("role", "status");
        menu.after(status);
        return function announce() {
            const count = menu.querySelectorAll(".dropdown-item").length;
            status.textContent = count === 0 ? "No family suggestions"
                : count === 1 ? "1 family suggestion" : count + " family suggestions";
        };
    }

    // an edit modal a live search (live_search.js) brings in later gets it on first focus
    document.addEventListener('focusin', function (event) {
        if (['sample_family_name', 'family_new_name', 'family'].indexOf(event.target.id) >= 0) {
            attachFamilyAutocomplete(event.target);
        }
    });

    attachFamilyAutocomplete(document.getElementById('sample_family_name'));
    attachFamilyAutocomplete(document.getElementById('family_new_name'));
    attachFamilyAutocomplete(document.getElementById('family'));
});
