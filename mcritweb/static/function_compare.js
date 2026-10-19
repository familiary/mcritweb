// The function comparison page (data.match_functions), issue #74.
//
// main_duo.js renders the two control flow graphs and colours their blocks by
// how well they matched; this adds what happens *between* the graphs:
//
//   * synchronised panning and zooming, relative to each graph's own fit-to-view
//   * linked highlighting - hovering a block outlines the blocks it matched on
//     the other side, clicking it centres the other graph on its first partner
//   * a "combined" view in the manner of BinDiff: both functions merged into one
//     graph, served by explore.fetchCombinedDotGraph and rendered here
//
// main_duo.js calls onGraphShown(graph_id) once a graph is on screen and records
// its zoom behaviour in graph_zooms; nothing else of it is touched. Everything
// here needs d3 v3, graphlib-dot and dagre-d3 from trace_CFG/lib, loaded by the
// template before this file.

var FunctionCompare = (function () {
  var config = {nodeMatches: {a: {}, b: {}}, combinedUrl: null};
  var isSyncEnabled = true;
  var isMirroring = false;      // re-entrancy guard while one graph updates the other
  var combined = null;          // {graph, zoom, svg, inner} once the combined view rendered
  var isCombinedLoading = false;

  var OTHER = {a: "b", b: "a"};

  function init(options) {
    config.nodeMatches = options.nodeMatches || config.nodeMatches;
    config.combinedUrl = options.combinedUrl || null;

    var syncToggle = document.getElementById("syncGraphs");
    if (syncToggle) {
      isSyncEnabled = syncToggle.checked;
      syncToggle.addEventListener("change", function () {
        isSyncEnabled = this.checked;
      });
    }
    d3.selectAll("input[name=viewMode]").on("change", function () {
      setViewMode(this.value);
    });

    var resetButton = document.getElementById("resetView");
    if (resetButton) {
      resetButton.addEventListener("click", resetView);
    }
  }

  // Put every graph back to the fit-to-pane it was rendered with. Needed because the
  // two graphs are fitted independently - a 40-block function and a 90-block one get
  // different initial scales - and the sync mirrors the *ratio* of a zoom, so that
  // difference is preserved by design however much you zoom. Without a way back, a
  // few wheel turns leave the two sides at scales that cannot be reconciled by hand.
  function resetView() {
    ["a", "b"].forEach(function (graph_id) {
      var entry = graph_zooms[graph_id];
      if (!entry) {
        return;
      }
      var translate = [0, 0];
      entry.zoom.scale(entry.initialScale).translate(translate);
      entry.lastScale = entry.initialScale;
      entry.lastTranslate = translate.slice();
      applyTransform(entry, translate, entry.initialScale);
    });
    if (combined !== null && combined.initialScale !== undefined) {
      combined.zoom.scale(combined.initialScale).translate([0, 0]);
      combined.inner.attr("transform", "translate(0,0)scale(" + combined.initialScale + ")");
    }
  }

  // --- synchronised pan and zoom ------------------------------------------------

  function applyTransform(entry, translate, scale) {
    entry.inner.attr("transform", "translate(" + translate + ")scale(" + scale + ")");
  }

  // Called by main_duo.js once graph `graph_id` is rendered and fitted to its pane.
  function onGraphShown(graph_id) {
    var entry = graph_zooms[graph_id];
    if (!entry) {
      return;
    }
    entry.lastScale = entry.zoom.scale();
    entry.lastTranslate = entry.zoom.translate().slice();
    // The combined view hides the side-by-side panes; a graph fitted while hidden
    // measures a zero-sized pane and ends up with a negative scale. So the switch
    // is offered only once both graphs are on screen.
    if (graph_zooms.a && graph_zooms.b) {
      var combinedToggle = document.getElementById("viewCombined");
      if (combinedToggle) {
        combinedToggle.disabled = false;
      }
    }
    // Replace the zoom listener main_duo.js installed with one that also drives
    // the other graph. What is mirrored is the *change*: the scale factor and the
    // pan in screen pixels. Both graphs are fitted to their panes independently and
    // a click may have centred one of them elsewhere, so absolute values would snap
    // the other graph back on the next wheel turn.
    entry.zoom.on("zoom", function () {
      var scale = d3.event.scale;
      var translate = d3.event.translate.slice();
      applyTransform(entry, translate, scale);
      var scaleFactor = scale / entry.lastScale;
      var delta = [translate[0] - entry.lastTranslate[0], translate[1] - entry.lastTranslate[1]];
      entry.lastScale = scale;
      entry.lastTranslate = translate;
      if (!isSyncEnabled || isMirroring) {
        return;
      }
      var other = graph_zooms[OTHER[graph_id]];
      if (!other || other.lastScale === undefined) {
        return;
      }
      isMirroring = true;
      var otherScale = other.lastScale * scaleFactor;
      var otherTranslate;
      if (Math.abs(scaleFactor - 1) < 1e-9) {
        // a pan: the same pixel offset on both sides
        otherTranslate = [other.lastTranslate[0] + delta[0], other.lastTranslate[1] + delta[1]];
      } else {
        // a zoom keeps one screen point fixed; recover it from this graph's before
        // and after (t' = p - (p - t) * k) and zoom the other graph around the same
        // point from *its own* translate, so graphs that were offset against each
        // other (after a click-to-centre, say) stay offset rather than drifting
        var lastTranslate = [translate[0] - delta[0], translate[1] - delta[1]];
        var fixedPoint = [
          (translate[0] - scaleFactor * lastTranslate[0]) / (1 - scaleFactor),
          (translate[1] - scaleFactor * lastTranslate[1]) / (1 - scaleFactor),
        ];
        otherTranslate = [
          fixedPoint[0] - (fixedPoint[0] - other.lastTranslate[0]) * scaleFactor,
          fixedPoint[1] - (fixedPoint[1] - other.lastTranslate[1]) * scaleFactor,
        ];
      }
      other.zoom.scale(otherScale).translate(otherTranslate);
      other.lastScale = otherScale;
      other.lastTranslate = otherTranslate;
      applyTransform(other, otherTranslate, otherScale);
      isMirroring = false;
    });
    installLinkedHighlighting(graph_id);
  }

  // --- linked highlighting --------------------------------------------------------

  function nodesOf(graph_id) {
    return d3.selectAll("#graphContainer_" + graph_id + " g.node.enter");
  }

  function partnersOf(graph_id, nodeId) {
    var matches = config.nodeMatches[graph_id] || {};
    return matches[nodeId] || [];
  }

  function installLinkedHighlighting(graph_id) {
    // namespaced listeners, so the ones main_duo.js installed keep running
    nodesOf(graph_id)
      .on("mouseover.link", function (nodeId) {
        var partners = partnersOf(graph_id, nodeId);
        nodesOf(OTHER[graph_id]).classed("matched", function (otherId) {
          return partners.indexOf(otherId) !== -1;
        });
        d3.select(this).classed("matched", partners.length > 0);
      })
      .on("mouseout.link", function () {
        nodesOf("a").classed("matched", false);
        nodesOf("b").classed("matched", false);
      })
      .on("click.link", function (nodeId) {
        var partners = partnersOf(graph_id, nodeId);
        if (partners.length > 0) {
          centerOn(OTHER[graph_id], partners[0]);
        }
      });
  }

  // Pan graph `graph_id` so that node `nodeId` sits in the middle of its pane, at
  // the current scale. Deliberately not mirrored to the other graph.
  function centerOn(graph_id, nodeId) {
    var entry = graph_zooms[graph_id];
    if (!entry) {
      return;
    }
    var node = nodesOf(graph_id).filter(function (d) { return d === nodeId; });
    if (node.empty()) {
      return;
    }
    var position = d3.transform(node.attr("transform")).translate;
    var scale = entry.zoom.scale();
    var bounds = entry.svg.node().getBoundingClientRect();
    var translate = [bounds.width / 2 - position[0] * scale, bounds.height / 2 - position[1] * scale];
    entry.zoom.translate(translate);
    entry.lastTranslate = translate.slice();
    applyTransform(entry, translate, scale);
  }

  // --- combined view ----------------------------------------------------------------

  function setViewMode(mode) {
    var sideBySide = document.getElementById("xcfg_container");
    var combinedPane = document.getElementById("xcfg_combined");
    if (!sideBySide || !combinedPane) {
      return;
    }
    if (mode === "combined") {
      sideBySide.style.display = "none";
      combinedPane.style.display = "block";
      if (combined === null) {
        loadCombined();
      }
    } else {
      combinedPane.style.display = "none";
      sideBySide.style.display = "block";
    }
  }

  function loadCombined() {
    if (isCombinedLoading || !config.combinedUrl) {
      return;
    }
    isCombinedLoading = true;
    d3.select("#loading_c").classed("hidden", false);
    d3.xhr(config.combinedUrl).header("Content-Type", "text/plain").get(function (error, result) {
      isCombinedLoading = false;
      d3.select("#loading_c").classed("hidden", true);
      if (error || !result.responseText) {
        d3.select("#combined_error").classed("hidden", false);
        return;
      }
      // the same line-ending fixup main_duo.js applies before parsing
      var dotFile = result.responseText.replace(/\\l/g, "\n");
      renderCombined(graphlibDot.parse(dotFile));
    });
  }

  function renderCombined(graph) {
    var svg = d3.select("#graphContainer_c");
    var inner = d3.select("#graphContainer_c g");
    var renderer = new dagreD3.Renderer();
    renderer.run(graph, inner);

    // colours and dashes come as attributes on the dot graph, see
    // functiondiff.build_combined_dot_graph
    d3.selectAll("#graphContainer_c g.node.enter").each(function (nodeId) {
      var attributes = graph.node(nodeId);
      var node = d3.select(this);
      if (attributes.fillcolor) {
        node.select("rect").style("fill", attributes.fillcolor);
      }
      node.classed("only-in-" + attributes.side, attributes.side === "a" || attributes.side === "b");
    });
    d3.selectAll("#graphContainer_c g.edgePath.enter").each(function (edgeId) {
      if (!graph.hasEdge(edgeId)) {
        d3.select(this).remove();
        return;
      }
      var attributes = graph.edge(edgeId);
      var edge = d3.select(this);
      if (attributes.color) {
        edge.select("path").style("stroke", attributes.color);
      }
      edge.classed("dashed", attributes.style === "dashed");
    });
    d3.selectAll("#graphContainer_c g.edgeLabel.enter").each(function (edgeId) {
      if (!graph.hasEdge(edgeId)) {
        d3.select(this).remove();
      }
    });

    // fit to the pane, as main_duo.js does for the two single graphs
    var bbox = svg.node().getBBox();
    var bounds = svg.node().getBoundingClientRect();
    var initialScale = Math.min((bounds.width - 16) / bbox.width, (bounds.height - 16) / bbox.height);
    var zoom = d3.behavior.zoom().on("zoom", function () {
      inner.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
    });
    svg.call(zoom).on("dblclick.zoom", null);
    zoom.scale(initialScale).event(svg);
    combined = {graph: graph, zoom: zoom, svg: svg, inner: inner, initialScale: initialScale};

    installCombinedTooltip(graph);
  }

  // A matched block shows A's instructions; where B's differ they travel in the
  // node's `comment` and are shown while hovering the block.
  function installCombinedTooltip(graph) {
    var tooltip = d3.select("#tooltip_c");
    d3.selectAll("#graphContainer_c g.node.enter")
      .on("mouseover.tooltip", function (nodeId) {
        var attributes = graph.node(nodeId);
        if (!attributes.comment) {
          return;
        }
        var position = d3.mouse(d3.select("#xcfg_combined").node());
        tooltip
          .style("left", (position[0] + 20) + "px")
          .style("top", (position[1] + 20) + "px")
          .classed("hidden", false)
          .select("#value_c")
          .text("B:\n" + attributes.comment);
      })
      .on("mouseout.tooltip", function () {
        tooltip.classed("hidden", true);
      });
  }

  return {
    init: init,
    onGraphShown: onGraphShown,
    centerOn: centerOn,
    setViewMode: setViewMode,
    resetView: resetView,
  };
})();

// the hook main_duo.js calls after rendering a graph
function onGraphShown(graph_id) {
  FunctionCompare.onGraphShown(graph_id);
}
