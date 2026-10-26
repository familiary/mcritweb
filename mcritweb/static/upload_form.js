// The upload form: on the query and submit pages, and in the drop overlay every other page
// carries (templates/table/submit_or_query_dropzone.html, templates/base.html). It was
// inline, in every page's HTML; submit_or_query_dropzone_js() queues it with later() now
// (ADR-0021), so a browser fetches it once, and it runs after jQuery. What only the
// template knows - the URLs, whether the form waits for Dropzone - it writes on the form
// as data- attributes. The minhash slider's label is set from the template, so that it
// follows the slider before this has run.
//
// A radio changed before this has run changes nothing at once: the ready handler below
// then sets the fields from the radios, as it does for a page the browser restores.

function show_extended(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementById("extendedFields")).slideDown();
    }
}

function hide_extended(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementById("extendedFields")).slideUp();
    }
}

function show_submit(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementsByClassName("submit-form-exclusive")).slideDown();
        $(document.getElementsByClassName("query-form-exclusive")).slideUp();
    }
}

function hide_submit(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementsByClassName("submit-form-exclusive")).slideUp();
        $(document.getElementsByClassName("query-form-exclusive")).slideDown();
    }
}

function fill_form(data) {
    data = JSON.parse(data);
    // set dumped/unmapped/smda
    if (data["dump"]) {
        $("#dumped").click();
    } else if (data["smda"]) {
        $("#smda").click();
    } else {
        $("#unmapped").click();
    }
    // set bitness
    if (data["bitness"] == 32) {
        $("#bitness32").click();
    } else if (data["bitness"] == 64) {
        $("#bitness64").click();
    }
    // set base address
    $("#base_addr").val(data["base_addr"]);
    // set family and version
    $("#family").val(data["family"]);
    $("#version").val(data["version"]);
}

function mcritInitDropzone() {
    const myDropzone = Dropzone.forElement("#myDropzone");
    const form = document.getElementById("dropzone-additional-fields-form");

    myDropzone.on("removedfile", function (file) {
        if (this.files.length == 0) {
            $("#submit-dropzone").prop("disabled", true);
        }
    });

    myDropzone.on("addedfile", function (file) {
        // ensure only one file in dropzone
        if (this.files.length > 1) {
            this.removeFile(this.files[0]);
        }

        // activate button
        $("#submit-dropzone").prop("disabled", false);

        // fill form with data derived from file name
        const filename = file.name;

        let xhr = new XMLHttpRequest();
        xhr.open("POST", form.dataset.filenameInfo);

        xhr.setRequestHeader("Accept", "application/json");
        xhr.setRequestHeader("Content-Type", "application/json");
        // a JSON body carries no form field, so the token travels as a header
        xhr.setRequestHeader("X-CSRFToken", csrfToken());

        xhr.onload = () => fill_form(xhr.responseText);

        myDropzone.processQueue();
        var form_data = $('#dropzone-additional-fields-form').serializeArray();

        let data = {
            "filename": filename,
            "file_header": file.upload.header,
            // the window cut around "metadata", which a fixed prefix never reaches in an
            // .smda report - empty for anything else
            "file_metadata": file.upload.header_metadata,
            "form": form_data
        };

        xhr.send(JSON.stringify(data));
    });

    myDropzone.on('sending', function (file, xhr, formData) {
        // Append all form inputs to the formData Dropzone will POST
        $("#submit-spinner").show();
        var data = $('#dropzone-additional-fields-form').serializeArray();
        $.each(data, function (key, el) {
            formData.append(el.name, el.value);
        });
    });

    $("#submit-dropzone").click(function (e) {
        e.preventDefault();
        e.stopPropagation();
        myDropzone.processQueue();
    });

    myDropzone.on('error', function (file, response) {
        location.reload();
    });

    myDropzone.on('success', function (file, response) {
        window.location.href = response;
    });
}

// The drop overlay, its dialog and form, is not in the page: base.html leaves a slot for
// it, and on the first drag it is fetched from data.drop_overlay (templates/drop_overlay.html)
// while Dropzone's stylesheet and library are, and put in the slot once both are in. The
// handlers above wait for it. Issue #63.
function mcritLoadDropzone(done) {
    if (window.mcritDropzoneReady) { done(); return; }
    if (window.mcritDropzoneLoading) {
        // dragover fires every few milliseconds while a file is held over the page
        if (window.mcritDropzoneLoading.indexOf(done) < 0) { window.mcritDropzoneLoading.push(done); }
        return;
    }
    const slot = document.getElementById("drop-overlay-slot");
    window.mcritDropzoneLoading = [done];
    if (!document.getElementById("mcrit-dropzone-css")) {
        var css = document.createElement("link");
        css.id = "mcrit-dropzone-css";
        css.rel = "stylesheet";
        css.href = slot.dataset.dropzoneCss;
        // ahead of the page's own styles, where the eager pages link it: its `.dropzone` rule
        // is as specific as the zone's style and would win if it came last
        document.head.insertBefore(css, document.head.querySelector("link[rel=stylesheet], style"));
    }
    var pending = 0;
    var failed = false;
    function settle(ok) {
        failed = failed || !ok;
        pending -= 1;
        if (pending > 0) {
            return;
        }
        var waiting = window.mcritDropzoneLoading;
        // after a failure the next drag tries again, for what is still missing
        window.mcritDropzoneLoading = null;
        if (failed) {
            return;
        }
        const form = document.getElementById("dropzone-additional-fields-form");
        window.mcritDropzoneOptions = JSON.parse(form.dataset.dropzoneOptions);
        Dropzone.options.myDropzone = window.mcritDropzoneOptions;
        var element = document.getElementById("myDropzone");
        element.classList.add("dropzone");
        if (!element.dropzone) {
            new Dropzone(element, window.mcritDropzoneOptions);
        }
        mcritInitDropzone();
        $("#submitFileModal").on("hidden.bs.modal", function () {
            Dropzone.forElement("#myDropzone").removeAllFiles();
        });
        window.mcritDropzoneReady = true;
        waiting.forEach(function (callback) { callback(); });
    }
    if (!window.Dropzone) {
        pending += 1;
        var script = document.createElement("script");
        // the project's patched build: it sends the head of the file the form is pre-filled from
        script.src = slot.dataset.dropzoneJs;
        script.onerror = function () {
            script.remove();
            settle(false);
        };
        script.onload = function () {
            Dropzone.autoDiscover = false;
            settle(true);
        };
        document.head.appendChild(script);
    }
    if (!document.getElementById("submitFileModal")) {
        pending += 1;
        // same-origin, and so the session's: a session that has ended is sent to the login
        // page, which is not the overlay
        fetch(slot.dataset.overlay, {headers: {Accept: "text/html"}})
            .then(function (response) {
                return response.ok && !response.redirected ? response.text() : "";
            })
            .then(function (markup) {
                const overlay = document.createElement("template");
                overlay.innerHTML = markup;
                if (!overlay.content.getElementById("dropzone-additional-fields-form")) {
                    return false;
                }
                slot.after(overlay.content);
                mcritWireUploadForm(document.getElementById("dropzone-additional-fields-form"));
                return true;
            })
            // offline, or the server gone
            .catch(function () { return false; })
            .then(settle);
    }
    if (pending === 0) {
        pending = 1;
        settle(true);
    }
}

// The overlay's slider has the template's handler name, and no template script to define it.
if (typeof window.handleSliderChange_dropzone !== "function") {
    window.handleSliderChange_dropzone = function (val) {
        const mapping = JSON.parse(document.getElementById("minhashBandRange_dropzone").dataset.labels);
        document.getElementById("minhashBandRange-currentSelection_dropzone").innerHTML = "&nbsp;" + mapping[val];
    };
}

// The family type-ahead of the overlay's form. A page whose edit modals have one already
// queued static/family_names.js, which attaches to the field on its first focus; any other
// page gets it now, and it attaches at once.
function mcritFamilyTypeAhead(form) {
    const src = form.dataset.familyNamesJs;
    if (!src || mcritScripts.queued.indexOf(src) >= 0) {
        return;
    }
    mcritScripts.queued.push(src);
    window.mcritFamilyNamesUrl = form.dataset.familyNames;
    var script = document.createElement("script");
    script.src = src;
    document.head.appendChild(script);
}

// The radios' handlers, and the state the checked radios ask for: for the form a page
// carries, once this file has run, and for the overlay's, once it is in the page.
function mcritWireUploadForm(form) {
    const handlers = {query_form: hide_submit, submit_form: show_submit, unmapped: hide_extended, dumped: show_extended, smda: show_extended};
    Object.keys(handlers).forEach(function (id) {
        const radio = document.getElementById(id);
        if (radio) {
            radio.addEventListener("change", function () { handlers[id](radio); });
        }
    });

    whenReady(function () {
        // the state the checked radios ask for, whichever way they got checked: a toggle
        // before this file had run is left to this, and a page the browser restores comes
        // back with its radios as they were. SMDA shows the fields as Dumped does. Set,
        // not slid: this runs after the page has been painted, where a slide is a layout
        // shift on the upload pages, and in the overlay, which is not shown yet, 400 ms of
        // animation no one sees.
        if ($("#dumped").prop("checked") || $("#smda").prop("checked")) {
            $(document.getElementById("extendedFields")).show();
        }
        if ($("#submit_form").prop("checked")) {
            $(document.getElementsByClassName("submit-form-exclusive")).show();
            $(document.getElementsByClassName("query-form-exclusive")).hide();
        }
        if ($("#query_form").prop("checked")) {
            $(document.getElementsByClassName("submit-form-exclusive")).hide();
            $(document.getElementsByClassName("query-form-exclusive")).show();
        }
        if (form.dataset.lazy === undefined) {
            mcritInitDropzone();
        } else {
            mcritFamilyTypeAhead(form);
        }
    });
}

(function () {
    const form = document.getElementById("dropzone-additional-fields-form");
    if (form) {
        mcritWireUploadForm(form);
    }

    // the drop overlay
    if (!document.getElementById("drop-overlay-slot")) {
        return;
    }
    whenReady(function () {
        function showDropModal() {
            $("#submitFileModal").modal("show");
        }
        function carriesFiles(e) {
            var types = (e.dataTransfer && e.dataTransfer.types) || [];
            return Array.prototype.indexOf.call(types, "Files") >= 0;
        }
        // A file let go before the overlay is in would be the browser's, which opens it in
        // place of the page: the page takes it while the overlay is on its way, and hands it
        // to the zone once it is.
        var heldFile = null;
        function dropHeldFile() {
            showDropModal();
            if (heldFile) {
                Dropzone.forElement("#myDropzone").addFile(heldFile);
                heldFile = null;
            }
        }

        // the drop overlay is on every page, but its form and Dropzone itself (its script
        // and stylesheet) are only fetched once a file is actually dragged in - issue #63
        window.addEventListener('dragover', function (e) {
            var dt = e.dataTransfer;
            if (dt.effectAllowed == "copy" || dt.dropEffect == "copy") {
                mcritLoadDropzone(showDropModal);
            }
            if (!window.mcritDropzoneReady && carriesFiles(e)) {
                e.preventDefault();
                dt.dropEffect = "copy";
            }
        });
        window.addEventListener('drop', function (e) {
            if (!window.mcritDropzoneReady && carriesFiles(e) && e.dataTransfer.files.length) {
                e.preventDefault();
                heldFile = e.dataTransfer.files[0];
                mcritLoadDropzone(dropHeldFile);
            }
        });
    });
})();
