// The drop zone of the pages it is the content of: the query, submit and import pages.
// There dropzone.js is queued with later() (ADR-0021) instead of loaded by a tag in the
// body, so that the first paint does not share the network with it, and this file is
// queued right after it. The zone keeps its `dropzone` class, and with it its look, from
// the first paint; the template parks the options flask-dropzone renders in
// window.mcritDropzoneOptions, since Dropzone is not there yet when they are read.
//
// Dropzone's auto-discovery would attach the zone without them. Run after
// DOMContentLoaded, it waits for the document to be complete, and a script the queue
// added holds that back until it has run: this attaches the zone first, with the page's
// options, and turns the discovery off.
Dropzone.autoDiscover = false;
(function () {
    const element = document.getElementById("myDropzone");
    if (element && !element.dropzone) {
        new Dropzone(element, window.mcritDropzoneOptions);
    }
})();
