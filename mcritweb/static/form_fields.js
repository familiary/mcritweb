// What the sign-in, registration, settings and upload forms say about a field, kept in step
// with what the page shows. Queued with later() by those pages (ADR-0021); everything binds
// to the document, so it holds for fields a page adds after it has run.
//
// aria-invalid follows the browser's own verdict, :user-invalid, which matches only once the
// user has left a field or tried to submit, so a screen reader hears "invalid" when a sighted
// user sees the red border and not before. A field the server refused
// (templates/table/field_error.html) comes marked already; editing it withdraws the mark and
// the message beside it, which no longer describe what is there.

(function () {
    function isField(element) {
        return element.matches && element.matches("input, select, textarea");
    }

    function syncValidity(field) {
        if (field.matches(":user-invalid")) {
            field.setAttribute("aria-invalid", "true");
        } else if (!field.hasAttribute("data-server-invalid")) {
            field.removeAttribute("aria-invalid");
        }
    }

    function withdrawServerVerdict(field) {
        if (!field.hasAttribute("data-server-invalid")) {
            return;
        }
        field.removeAttribute("data-server-invalid");
        field.removeAttribute("aria-invalid");
        // the message describes every field it names; it goes with the first edit to any of them
        const describedBy = (field.getAttribute("aria-describedby") || "").split(" ");
        describedBy.filter((id) => id.endsWith("-error")).forEach(function (id) {
            const message = document.getElementById(id);
            if (message) {
                message.hidden = true;
            }
            document.querySelectorAll("[aria-describedby~='" + id + "']").forEach(function (other) {
                const rest = other.getAttribute("aria-describedby").split(" ").filter((each) => each !== id);
                if (rest.length) {
                    other.setAttribute("aria-describedby", rest.join(" "));
                } else {
                    other.removeAttribute("aria-describedby");
                }
                if (other !== field && other.hasAttribute("data-server-invalid")) {
                    other.removeAttribute("data-server-invalid");
                    other.removeAttribute("aria-invalid");
                }
            });
        });
    }

    document.addEventListener("input", function (event) {
        if (!isField(event.target)) {
            return;
        }
        withdrawServerVerdict(event.target);
        syncValidity(event.target);
    });
    // focus events do not bubble, so these listen in the capture phase
    document.addEventListener("blur", function (event) {
        if (isField(event.target)) {
            syncValidity(event.target);
        }
    }, true);
    // a refused submit focuses the first invalid field, which is when :user-invalid starts to match
    document.addEventListener("focus", function (event) {
        if (isField(event.target)) {
            syncValidity(event.target);
        }
    }, true);
})();
