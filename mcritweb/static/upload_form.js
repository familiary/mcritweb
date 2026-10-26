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
    requireDumpFields();
}

function hide_extended(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementById("extendedFields")).slideUp();
    }
    requireDumpFields();
}

function show_submit(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementsByClassName("submit-form-exclusive")).slideDown();
        $(document.getElementsByClassName("query-form-exclusive")).slideUp();
    }
    requireDumpFields();
    nameSubmitButton();
}

function hide_submit(x) {
    if (x.checked && window.jQuery) {
        $(document.getElementsByClassName("submit-form-exclusive")).slideUp();
        $(document.getElementsByClassName("query-form-exclusive")).slideDown();
    }
    requireDumpFields();
    nameSubmitButton();
}

// The overlay's button says what the chosen mode does, as the query and submit pages' buttons
// do: it said "Submit" whichever was chosen, while the form opens on a query.
function nameSubmitButton() {
    const choice = document.getElementById("submit_form");
    if (choice) {
        document.getElementById("submit-dropzone-label").textContent = choice.checked ? "Submit" : "Query";
    }
}

// A memory dump is mapped at its base address, and one added to the database needs its
// bitness as well (views/params.py). The browser asks for both before the upload starts: the
// server's refusal used to come only after the file had been sent, and the page reloaded
// without the file and the fields. Only while "Dumped" is chosen, since a hidden field the
// form does not use must not hold it back.
function requireDumpFields() {
    const form = document.getElementById("dropzone-additional-fields-form");
    const dumped = document.getElementById("dumped").checked;
    const choice = document.getElementById("submit_form");
    const submitting = choice ? choice.checked : form.dataset.submitFields !== undefined;
    const baseAddress = document.getElementById("base_addr");
    baseAddress.required = dumped;
    if (dumped) {
        // up to 16 hex digits after any leading zeros: a 64 bit address, as the server takes it
        baseAddress.setAttribute("pattern", "\\s*(0[xX])?0*[0-9a-fA-F]{1,16}\\s*");
    } else {
        baseAddress.removeAttribute("pattern");
    }
    // one radio of a group being required makes the group required
    document.getElementById("bitness32").required = dumped && submitting;
}

// What the form has to say about the upload, beside its button: a file still to be chosen,
// or the server's reason for refusing one.
function sayAboutUpload(message) {
    document.getElementById("upload-feedback").textContent = message;
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
    prefill("base_addr", data["base_addr"]);
    prefill("family", data["family"]);
    prefill("version", data["version"]);
}

// A value the file declares goes into its field. One the file does not declare leaves the
// field as it is, since the page focuses the family field and people type before they drop:
// writing the probe's missing value emptied what they had typed. What an earlier file filled
// in and nobody changed is that file's, though, and goes with it.
function prefill(id, value) {
    const field = document.getElementById(id);
    if (value) {
        field.value = value;
        field.dataset.prefilled = value;
        return;
    }
    if (field.value === field.dataset.prefilled) {
        field.value = "";
    }
    delete field.dataset.prefilled;
}

function mcritInitDropzone() {
    const myDropzone = Dropzone.forElement("#myDropzone");
    const form = document.getElementById("dropzone-additional-fields-form");

    myDropzone.on("addedfile", function (file) {
        // ensure only one file in dropzone
        if (this.files.length > 1) {
            this.removeFile(this.files[0]);
        }
        sayAboutUpload("");

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

    // the button is disabled in the markup only until this has run: before, a press would
    // have sent the bare form to the page's own address
    $("#submit-dropzone").prop("disabled", false).click(function (e) {
        e.preventDefault();
        e.stopPropagation();
        // the browser's own check of the fields, which says what is missing and focuses it
        if (!form.reportValidity()) {
            return;
        }
        if (myDropzone.getQueuedFiles().length === 0) {
            sayAboutUpload("Choose a file first.");
            const chooser = document.querySelector("#myDropzone .dz-button");
            if (chooser) {
                chooser.focus();
            }
            return;
        }
        sayAboutUpload("");
        myDropzone.processQueue();
    });

    myDropzone.on('error', function (file, message, xhr) {
        $("#submit-spinner").hide();
        if (!xhr) {
            // Dropzone refused the file itself, too large for one; it cannot be sent
            sayAboutUpload(String(message));
            return;
        }
        // The server says why in plain text (views/utility.py, refuse_upload). The page
        // used to reload to show it as a flash, which dropped the file and every field;
        // the file stays queued instead, to go again with the next press.
        const plain = (xhr.getResponseHeader("Content-Type") || "").indexOf("text/plain") === 0;
        sayAboutUpload(plain && message ? String(message) : "The upload failed (" + xhr.status + "). Please try again.");
        file.status = Dropzone.QUEUED;
        if (file.previewElement) {
            file.previewElement.classList.remove("dz-error", "dz-processing");
        }
    });

    myDropzone.on('success', function (file, response) {
        window.location.href = response;
    });
}

// The overlay's form fetches Dropzone's stylesheet and library on the first drag instead of
// with every page; the handlers above wait for it. Issue #63.
function mcritLoadDropzone(done) {
    if (window.mcritDropzoneReady) { done(); return; }
    if (window.mcritDropzoneLoading) {
        // dragover fires every few milliseconds while a file is held over the page
        if (window.mcritDropzoneLoading.indexOf(done) < 0) { window.mcritDropzoneLoading.push(done); }
        return;
    }
    const form = document.getElementById("dropzone-additional-fields-form");
    window.mcritDropzoneLoading = [done];
    if (!document.getElementById("mcrit-dropzone-css")) {
        var css = document.createElement("link");
        css.id = "mcrit-dropzone-css";
        css.rel = "stylesheet";
        css.href = form.dataset.dropzoneCss;
        // ahead of the page's own styles, where the eager pages link it: its `.dropzone` rule
        // is as specific as the zone's style and would win if it came last
        document.head.insertBefore(css, document.head.querySelector("link[rel=stylesheet], style"));
    }
    var script = document.createElement("script");
    // the project's patched build: it sends the head of the file the form is pre-filled from
    script.src = form.dataset.dropzoneJs;
    script.onerror = function () {
        // let the next drag try again
        window.mcritDropzoneLoading = null;
    };
    script.onload = function () {
        Dropzone.autoDiscover = false;
        Dropzone.options.myDropzone = window.mcritDropzoneOptions;
        var element = document.getElementById("myDropzone");
        element.classList.add("dropzone");
        if (!element.dropzone) {
            new Dropzone(element, window.mcritDropzoneOptions);
        }
        mcritInitDropzone();
        window.mcritDropzoneReady = true;
        var waiting = window.mcritDropzoneLoading;
        window.mcritDropzoneLoading = null;
        waiting.forEach(function (callback) { callback(); });
    };
    document.head.appendChild(script);
}

(function () {
    const form = document.getElementById("dropzone-additional-fields-form");
    if (!form) {
        return;
    }
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
        // shift on the upload pages, and on every other page, whose form is in the closed
        // drop overlay, 400 ms of animation no one sees.
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
        requireDumpFields();
        nameSubmitButton();
        if (form.dataset.lazy === undefined) {
            mcritInitDropzone();
        }
    });

    // the drop overlay
    if (!document.getElementById("submitFileModal")) {
        return;
    }
    whenReady(function () {
        function showDropModal() {
            $("#submitFileModal").modal("show");
        }
        function clearDropzone(e) {
            if (!window.Dropzone) { return; }
            const myDropzone = Dropzone.forElement("#myDropzone");
            myDropzone.removeAllFiles();
        }

        $("#submitFileModal").on("hidden.bs.modal", function () {
            clearDropzone();
        });

        // the drop overlay is on every page, but Dropzone itself (its script and
        // stylesheet) is only fetched once a file is actually dragged in - issue #63
        window.addEventListener('dragover', function (e) {
            var dt = e.dataTransfer;
            if (dt.effectAllowed == "copy" || dt.dropEffect == "copy") {
                mcritLoadDropzone(showDropModal);
            }
        });
    });
})();
