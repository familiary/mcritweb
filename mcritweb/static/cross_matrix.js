// The cross compare matrix, for the keyboard and the pointer: the tooltip of a score, and
// the arrow keys. Queued with later() from result_cross.html, after jQuery and before
// Bootstrap; the handlers are delegated, so they also reach the matrices of the tabs that
// are filled in once their tab is shown.

// A matrix cell's tooltip names both of its samples. That part is the same along a whole
// row and down a whole column, so it is not written into every cell of every matrix
// (issue #198): a cell carries its score line, a row the line naming its sample, and the
// two are put together when the pointer or focus first reaches the cell. The cell's own
// row names the first sample and the row at the cell's column the second - rows and
// columns come in the same order. hint.css shows the text as it is (white-space: pre), so
// the line breaks and indentation are the ones the template used to write into the
// attribute.
whenReady(function () {
    $(document).on("mouseover focusin", "#pills-tabContent tr.sample-row .score-swatch", function () {
        var hint = this.getAttribute("data-hint");
        if (hint.indexOf("\n") >= 0) {
            return;
        }
        var row = this.closest("tr");
        var sampleRows = this.closest("table").querySelectorAll("tr.sample-row");
        var column = sampleRows[this.parentNode.cellIndex - (row.cells.length - sampleRows.length)];
        var indent = "\n            ";
        this.setAttribute("data-hint", hint + "\n" + indent + row.getAttribute("data-hint-sample") + indent + "\nvs.\n" + indent + column.getAttribute("data-hint-sample"));
    });

    // The matrix is one tab stop, and the score focused keeps it: the arrow keys move
    // between the scores, Home and End to the first and last of a row, and Enter follows a
    // score's link. A score was a coloured square a pointer alone could read or follow.
    $(document).on("keydown", "#pills-tabContent .score-swatch", function (event) {
        var cell = this.parentNode;
        var row = cell.parentNode;
        var target = null;
        if (event.altKey || event.ctrlKey || event.metaKey) {
            return;
        } else if (event.key === "ArrowLeft") {
            target = cell.previousElementSibling;
        } else if (event.key === "ArrowRight") {
            target = cell.nextElementSibling;
        } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            var other = event.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
            target = other !== null && other.classList.contains("sample-row") ? other.cells[cell.cellIndex] : null;
        } else if (event.key === "Home" || event.key === "End") {
            var scores = row.querySelectorAll(".score-swatch");
            target = scores[event.key === "Home" ? 0 : scores.length - 1].parentNode;
        } else {
            return;
        }
        event.preventDefault();
        var next = target ? target.querySelector(".score-swatch") : null;
        if (next !== null) {
            this.tabIndex = -1;
            next.tabIndex = 0;
            next.focus();
        }
    });
});
