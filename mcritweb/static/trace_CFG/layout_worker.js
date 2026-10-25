// mcritweb: runs dagre's layout of a function graph off the page's main thread, so the
// function page keeps responding while a large graph is laid out. The page measures the
// blocks (that needs the DOM), posts the measured graph here, and draws what comes back.
// The layout is the vendored dagre's own, with its default settings, as on the page.

// the dagre-d3 bundle looks for d3 on `window`, which a worker does not have; it only
// needs d3 to draw, and nothing is drawn here
self.window = self;
importScripts("lib/dagre-d3.min.js");

self.onmessage = function (event) {
  var request = event.data;
  try {
    var graph = dagreD3.json.decode(request.nodes, request.edges, request.type);
    graph.graph(request.value);
    var laidOut = dagreD3.layout().run(graph);
    var answer = dagreD3.json.encode(laidOut);
    answer.value = laidOut.graph();
    self.postMessage({ graph: answer });
  } catch (error) {
    self.postMessage({ error: String(error && error.stack || error) });
  }
};
