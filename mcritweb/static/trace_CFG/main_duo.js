//TODO: Make it the mainModule
//expose public variables and functions inside this nameSpace
// window.onload = function() {
  
  // Colors to depict different cycles
  // Bluish colors are removed since gradient also uses blue color
  var colores_g = [ "#dc3912", "#ff9900", "#109618", "#990099", 
    "#0099c6", "#dd4477", "#66aa00", "#b82e2e", "#994499",
    "#22aa99", "#aaaa11", "#6633cc", "#e67300", "#8b0707", "#651067", "#329262"];

  // dict for all nodes, edges, and edgelabels
  // keys are Id of the svg g elements, values are the g elements
  
  nodesAll = {}; 
  edgesAll = {};
  edgeLabelsAll = {}; 

  // mcritweb: issue #69 - this page draws two graphs, so every per-graph store
  // needs one of its own. Sharing a single set of the dicts above let the panel
  // that finished loading second overwrite the first's entries for every block
  // offset the two functions have in common, and sharing loopsObj let it erase
  // the first's loops outright.
  // "colors" records the diff colour each block was rendered with, so the loop
  // and cycle highlights can be taken back off again without leaving the graph
  // uncoloured.
  // "container" and "pane" are the panel's two elements - the svg its graph is drawn
  // in, and the half of the page holding that - so a handler can find its own panel's
  // markup instead of hardcoding one side. "rendered" is the graph dagre actually laid
  // out, which is the one the edge ids in the DOM belong to.
  var cfgPanels = {
    a: {graph: null, rendered: null, loops: [], nodes: {}, edges: {}, edgeLabels: {}, colors: {},
        container: "#graphContainer_a", pane: "#xcfg_left"},
    b: {graph: null, rendered: null, loops: [], nodes: {}, edges: {}, edgeLabels: {}, colors: {},
        container: "#graphContainer_b", pane: "#xcfg_right"}
  };

  // Points the shared dicts at one panel. The vendored modules and most of the
  // code below reach for them by name, so a panel is made current for the span of
  // the code that fills it rather than every reader being rewritten.
  //
  // Be clear about what this does and does not fix. The per-panel dicts are now
  // private and lossless - that is the issue #69 bug. But these four globals still
  // end up pointing at whichever panel rendered last, and which one that is is still
  // a race: 12 reloads of the same comparison page gave b 10 times and a twice. No
  // reachable reader observes it today, because every remaining one is dead code on
  // this page - the node-drag and brush handlers are behind controls the duo template
  // does not render (`#enableNodeDrag` is inside an HTML comment, `#enableBrush` and
  // `#countEncoding` appear nowhere), and `setupTrace()` and `getCodefromGraph()` have
  // no live call site. Everything that *is* reachable - the hover, the tooltip, the
  // edge click and the Backspace keybinding - takes its panel from `cfgPanels`, as
  // does the highlight code below. So this is latent, not active - but anyone wiring
  // up `setupTrace` has to take the panel from `cfgPanels` rather than trusting these.
  //
  // The dead code is not harmless just for being dead. `setupTrace` is what fills
  // `nodeToTextGroups`, and the three taint highlighters that read it write their
  // result into `innerHTML`; they escape now (see `escapeHtml`), and they have to
  // keep escaping, because wiring `setupTrace` up is all it would take to reach them.
  function usePanel(panel){
    nodesAll = panel.nodes;
    edgesAll = panel.edges;
    edgeLabelsAll = panel.edgeLabels;
    loopsObj = panel.loops;
    return panel;
  }

  // keys are nodeId and values are the array of p elements that are instances of the node/CFG block
  var nodeToTextGroups = {};

  // Dict that stores start and end address of all the CFG basic blocks with at least one instruction
  // key: nodeId, value: object with keys - startAddr and endAddr
  var nodesEndAddress = {};

  // key: startAddr, value - nodeId; for fast lookup of basic block corresponding to a block of trace
  var startAddrNode = {};

  // Modify these data structures to array of objects
  // Each object contains the array of nodes, collapse state,
  // id, name etc.

  //array of text block p elements in the sequence of the trace text
  var textBlocksArray = [];
  //array of offset object of the text blocks on the trace. Each object has keys p: d3 selection of p element, start:startOffset, end: endOffset
  //Used to auto-highlight the nodes on the graph when the user scrolls to the right
  var textBlocksOffset = [];

  var last_known_scroll_position = 0;  // to check if the previous scroll position was same; scroll events tend to be fired very fast
                                      // and are thus computationally intensive if not throttled 
  var ticking = false;  // to control/throttle the scroll auto-highlighting
  var last_known_panel_height = 0;

  var nodeGroups = [];   // Array of groups of nodes
  var isNodeGrpCollapsed = [];  // Stores the collapsed/non-collapsed state of these groups
  var nodeIdforGrp = []; //Stores the node id for each of these group if chosen by user 
                                  // Might also need to add new edges into the graph itself
                                  // Will change how we highlight codeview on the right 
                                  // Either collapse the corresponding blocks on the right side and change their linkage to the left side
                                  // OR Highlight everything on right side thats in the group on the left
  var nodeGroupMeta = [];   // Stores the group name for now
  var nodeGroupsArray = []; // Array of group of nodes  
  var currentTempGrp = [];  // Used as temporary storage while creating new groups

  var currentNode;    // Used to highlight the node when hovered or active, also used to delete the active node
  var currentNodePanel = null;  // mcritweb: issue #69 - which panel currentNode is in
  var currentText;    // Used to highlight the text when hovered or active
  g = null;  // The main graph 
  var graph_to_display_a = null;
  var graph_to_display_b = null;
  // mcritweb, issue #74: the zoom behaviour of each graph, keyed "a" / "b", so that
  // function_compare.js can synchronise and re-centre them. `zoom` below is a
  // single global that showGraph() overwrites per graph and cannot serve for that.
  var graph_zooms = {};

  var traceText; // The trace file
  var codes = []; // The array of blocks forming the text on the right side
  var currNodeHighlight = null; // used for highlighting individual node on hover
  var cycles; // stores cycles on the graph
  var currTextHighlight = []; // used for highlighting the text blocks on the right
  var isBrushEnabled = false; // turn brushing on/off: Brushing does not 
                              // work well with other panning and zooming interactions.
                              // It is disabled by default.

  var isTooltipEnabled = false; //Turns tooltip on/off: the tooltip that shows code when hovering over a node in the graph 

  var brushInitialized = false;
  var mouseOverColor = "white";
  var mouseOverGFill = "black";

  //The nodes mark the start and end points to be displayed at the CFG using trace sequence
  var currStartTextNode = null;
  var currEndTextNode = null;
  // The indexes record the index of the nodes in the textBlocksArray
  var currStartTextIndex = -1;
  var currEndTextIndex = -1;
  // The prev indexes track the previous marked text blocks
  var prevStartTextIndex = -1, prevEndTextIndex = -1;

  var rtDragStart = []; // Fast Dragging by right clicking or shift clicking; Right click not suitable for this purpose.
  var rtDragTranslate = [];
  var rtDragScale = [];
  var isRtDragStarted = false;

  var isCycleShown = false;

  var isLoopShown = false;

  var isTraceSupplied = false;

  var isHoverOnLeftPanel=false;

  //This variable stores whether the node's weighted degrees are encoded on the graph with color
  var isTripCountShown = true;

  //This variable stores whether the loop's boundary is enabled
  var isLoopBoundaryShown = true;

  //The list of all the currently tainted list of addresses
  var taintOutputList = [];

  // The list of all the currently and previously highlighted elements due to taint
  var prev_matched_tspans_graphs = [];
  var prev_matched_span_traces = [];
  var matched_tspans_graphs = [];
  var matched_span_traces = [];
  var match_list = {};

  is_node_dragging_enabled = false;

  var zoom = null;

  dotFile = null;
  loopsObj = null;
 
  // Load the dot file and trace file
  var f_dot = document.getElementById('fi_dot');
  var f_trace = document.getElementById('fi_trace')
  var fr = new FileReader();
  var fr1 = new FileReader();
  
  var default_file = "cfg.main.dot"; 

  var analysis_params = [];

  // Split(['#container', '#annotations'], {
  //       direction: 'vertical',
  //       sizes: [75, 25],
  //   });
  // Split(['#an1', '#an2', "#an3", "#an4"]);
  // MCRIT Split no longer needed
  // Split(['#left', '#right']);

    // Started navbar logic here
      /* Set the width of the side navigation to 250px */
    function openNav() {
        document.getElementById("mySidenav").style.width = "250px";
    }

    /* Set the width of the side navigation to 0 */
    function closeNav() {
        document.getElementById("mySidenav").style.width = "0";
    }

    // populate the dropdown list using the analysis.json file when initializing
    /*d3.json("../static/analysis.json", function(data) {
      analysis_params = data;
      console.log(analysis_params);

       d3.select("#analysisSelector")
          .selectAll("option")
          .data(analysis_params.scripts)
          .enter()
          .append("option")
          .attr("value", function(d) { return d.id; })
          .text(function(d) { return d.name; });


    });
    */

    d3.select("#mydropbtn")
      .on("click", function(){

        //Toggle the visibility
        var div = d3.select(".dropdown-content");

        if(div.style("display") == "none"){
          div.style("display", "block");
        } else {
          div.style("display", "none");
        }

        // div.style("display", "block");

    });

    d3.select("#encodingKeyHelpbtn")
      .on("click", function(){

        //Toggle the visibility
        var div = d3.select("#encodingKeyContainer");

        if(div.style("display") == "none"){
          div.style("display", "block");
        } else {
          div.style("display", "none");
        }

    }); 

      d3.select("#closeMenu").on("click", function(){
       d3.select(".dropdown-content").style("display", "none");
      });

    
  d3.selectAll(".sidenavHolderDiv h4")
    .on("click", function(){
      // get the sibling div
      // toggle the visibility

      var tempDiv = d3.select(this.parentNode).select("div");
      
      if(tempDiv.classed("hidden")){
        tempDiv.classed("hidden", false);
      } else {
        tempDiv.classed("hidden", true);
      }

    });
  // Ended navbar logic here



  //Initialize Analysis Highlighting
  d3.select("#doTaint")
        .on("click", function(){

       // alert("The backtaint and UERDetector libraries are not public and are not available. We are in the process of developing an interface to let you plug your analysis scripts. You can try these analysis in the demo site.");
       // return;

       // console.log(analysis_params);  

      // Get the type of analysis
      var sel = d3.select("#analysisSelector").node();
      var analysisType = sel.options[sel.selectedIndex].value;

      // if(analysisType == "allUERs" || 
      //   analysisType == "inUERs" ||
      //   analysisType == "outUERs"
      //   ){

      //   clearPrevHighlight();
      //   highlightUERs(analysisType);
      //   return;
      // }


		  // Remove previous taints
        for(var i=0; i<matched_tspans_graphs.length; i++){

          // matched_tspans_graphs[i].classed("taint", false);  
          
          // matched_tspans_graphs[i].rect.classed("taint", false);
          matched_tspans_graphs[i].rect.style("fill", "none");
          
          matched_tspans_graphs[i].tspan.classed("taint", false);

        }

        matched_tspans_graphs = [];

        // Remove taint on the trace
        // Use match_list  

        var textToHighlight = [];

        for (var nodeId in match_list) {
          if (match_list.hasOwnProperty(nodeId)) {

            if(isTraceSupplied){
              if(nodeId in nodeToTextGroups){
                textToHighlight = nodeToTextGroups[nodeId];
              }
            }

            // if(textToHighlight.length > 0){
            //   // var replaceStr = textToHighlight[0].text();
            //   // var replaceStr = replaceStr.replace("<span class = 'taint'>", '');
            //   // replaceStr = replaceStr.replace("</span>", '');

            //   var replaceStr = textToHighlight[0].node().innerHTML;
            //   replaceStr = replaceStr.replace(/<span[\s\S]*['"]>/ ,"");
            //   replaceStr = replaceStr.replace(/\n<\/span>/ ,"");

            // }

            if(textToHighlight.length > 0){
                var replaceStr = textToHighlight[0].text();
            }

            for(var i = 0; i<textToHighlight.length; i++){
              textToHighlight[i].text(replaceStr);
            }

          }
        }

        match_list = {};

        
        //Tainting is implemented on trace
        if(isTraceSupplied){
          var taintAddress = d3.select("#taintAddress").node().value.trim();
          // if address not empty
          if(taintAddress != ""){
            //Gather the tracetext and send it along with the address for tainting information

            //Construct the request object
            // var taintRequest = {trace: traceText, address: taintAddress};

            // Construct the request object along with the script parameters
            var taintRequest = {trace: traceText, address: taintAddress}; 

            // console.log(taintRequest);
            // console.log(analysis_params);  

            // Find the highlight script with the given id in analysis_params
            var scripts = analysis_params.scripts;
            for(var i=0; i<scripts.length; i++){
              if(scripts[i].id == analysisType){
                if(scripts[i].type == "instrHighlight" ){
                  taintRequest.scriptpath = scripts[i].scriptpath;
                  taintRequest.language = scripts[i].language;
                  taintRequest.outfilename = scripts[i].outfilename;
                } else {
                  return;
                }
                break;
              }
            }         

            // console.log(taintRequest);

            // Send request for taint information
            d3.xhr("../getBackTaint/")
            .header("Content-Type", "application/json")
            .post(JSON.stringify(taintRequest),
              function(err, result){
                // console.log("Response: ", result.responseText);

                var taintOutput = result.responseText;
                if(taintOutput.split("i:")[1] == null){
                	return;
                }

                taintOutputList = taintOutput.split("i:")[1].trim().split(/\s+/);

                // If the list of address is non-empty, then 
                if(taintOutputList.length>0){

                  //Update the max and value of the slider here
                  d3.select("#myTaintSlider").property("max", taintOutputList.length);
                  d3.select("#myTaintSlider").property("value", taintOutputList.length);
                  
                  d3.select("#sliderOutput").text(taintOutputList.length);

                	
                	var colorScale = d3.scale.linear()
                  .domain([0, taintOutputList.length - 1])
                  
                  // .range(["#8856a7", "#efedf5"])
                  // Make the color scale darker so that white text works
                  .range(["#8856a7", "#c0a5d1"])
                  .interpolate(d3.interpolateHcl);


                  // Go through the original graph, make a list of all the nodes with
                  // all the matching addresses contained in it,
                  // For the matching nodes, search through all the tspan elements
                  // If the tspan contains a matching address, then highlight it and add it to the list of modified tspans,
                  // Lookup the corresponding blocks in the trace
                  // Clear any existing spans (by giving them a class if the gradient is not needed),
                  // and then apply span on new elements.

                  // d3.selectAll("#graphContainer g.nodes tspan").each()

                  var graph_nodes = g.nodes();
                  match_list = {};
                  matched_tspans_graphs = [];

                  //Since an address only occurs in a CFG once, once an address is matched in some node, it 
                  // need not be considered in any other node.
                  // Here a boolean array the size of original address array keeps track of matched addresses
                  // A duplicate array with addresses sorted alphabetically is used so that earlier addresses are checked first
                  // If the address space is different, then in addition to alphabetical sorting it also needs to take into account variable length
                  // But within the same node, the order of addresses is probably in sorted order with just alphabetical sort
                  // if library code and regular code is not interleaved in the same node
                  
                  // An alternative way to creating a boolean array to track matched addresses is to delete elements from the duplicate array and that keeps on 
                  // decreasing the number of array accesses. Array should be traversed in reverse order if we want to use splice to delete elments from an array and
                  // still go through all the elements of an array.

                  var is_matched_index = new Array(taintOutputList.length).fill(false);
                  
                  /********* Don't sort the addresses ************/
                  /********* Use the original list and use the order in it ****/
                  // var sorted_addresses = taintOutputList.slice(0).sort();
                  var sorted_addresses = taintOutputList;
                  

                  var matched_count = taintOutputList.length;
                  // Since we work with sorted addresses, the addresses will be sorted in the match_list as well.

                  for(var i = 0; i<graph_nodes.length; i++){
                  
                      var nodeId = graph_nodes[i];
                      var node = g.node(nodeId);
                      var label = node.label;
                    
                      for(var k=0; k<is_matched_index.length; k++){

                      	if(is_matched_index[k]) {
                          continue;
                        }
                        var re = new RegExp(sorted_addresses[k], 'i');
                        // var re = new RegExp("\\b" + sorted_addresses[k] + "\\b", 'i');

                        if(re.test(label)){

                          console.log("match");

                          if(match_list[nodeId] == null){
                            match_list[nodeId] = [];
                          }
                          match_list[nodeId].push(k);
                          // If using the alternative approach, need to store the address itself since index 
                          // keeps on changing on every iteration of outer loop.

                          is_matched_index[k] = true;
                          matched_count--;

                          console.log(matched_count);

                        }  


                      }

                      // If using alternative approach, need to delete matched nodes here; Use splice to remove elements without
                      // creating any gaps

                      if(matched_count==0){
                      	break;
                      }

                  }

                  console.log(match_list);
                  console.log(taintOutputList);
                  console.log(sorted_addresses);

                  // Match list computed; Now apply highligting and store them
                  // Remove highlighting on previous matches
                  for (var nodeId in match_list) {
                    if (match_list.hasOwnProperty(nodeId)) {
                        
                      // We have the nodeId and we have the corresponding blocks in the traces 

                      var matched_addresses = match_list[nodeId];

                      var thisbbox = nodesAll[nodeId].select("text").node().getBBox();
                      var hasFunctionName = false;
                      if(nodesAll[nodeId].select("tspan").text().trim() != ""){
                        hasFunctionName = true;
                      }

                      //Find them in the tspans
                      nodesAll[nodeId].selectAll("tspan").each(function(d, i){
                        
                      	var text = d3.select(this).text();

                        // Check the matched addresses
                        for(var k = 0; k<matched_addresses.length; k++){
                          var re = new RegExp(sorted_addresses[matched_addresses[k]], 'i');
                          // var re = new RegExp("\\b" + sorted_addresses[matched_addresses[k]] + "\\b", 'i');

                          if(re.test(text)){
                            // Highlight it
                            // Add it to the list

                            // d3.select(this).style("fill", "white");
                            d3.select(this).classed("taint", true);

                            // d3.select(this).style("fill", colorScale(k));
                            

                            // var thisbbox = this.getBBox();
                            

                            // var y = thisClientRect.height*(i-1);
                            
                            var y = (i-1)*13 + 1;

                            if(hasFunctionName){
                              y = (i)*13 + 1;  
                            }

                            var x = 0;
                            var height = 14;
                            var width = thisbbox.width;

                            var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
                            rect.setAttribute("x", x);
                            rect.setAttribute("y", y);
                            rect.setAttribute("width", width);
                            rect.setAttribute("height", height);


                            // rect = nodesAll[nodeId].node().insertBefore(rect, this);
                            rect = nodesAll[nodeId].select("g").node().insertBefore(rect, nodesAll[nodeId].select("text").node());

                            rect = d3.select(rect);
                            rect.style("fill", colorScale(matched_addresses[k]))
                            .style("stroke", "none");

                            // rect.classed("taint", true);

                            // var rect = nodesAll[nodeId].select("g").append("rect")
                            // 	.attr("x", x)
                            // 	.attr("y", y)
                            // 	.attr("width", width)
                            // 	.attr("height", height)
                            // 	.classed("taint", true);

                            matched_tspans_graphs.push({rect: rect, tspan: d3.select(this)});
							                // matched_tspans_graphs.push(d3.select(this));
	
                            break;
                          
                          }

                        }


                      });



                      var textToHighlight = [];

                      // Highlight the trace text
                      
                      // Go through the lines;
                      // If the line contains a matching address, Put a span element in the line
                      // i.e Replace the text with <span>line</span>
                      // Prepare the array and join the array using "\n"

                      // Trace Backtaint Highlighting
                      if(isTraceSupplied){
                        if(nodeId in nodeToTextGroups){
                          textToHighlight = nodeToTextGroups[nodeId];
                        }
                      } 

                      // Previous highlighting removed at the start

                      // Start with any textblock and prepare the text to replace
                      // Then replace it in all instances
                      if(textToHighlight.length > 0){
                        var lines = textToHighlight[0].text().split('\n');

                        for(var l=0; l<lines.length; l++){
                          var line = lines[l];
                          for(var k=0; k<matched_addresses.length; k++){
                            var thisAddress = sorted_addresses[matched_addresses[k]];
                            var re = new RegExp("\\b" + thisAddress + "\\b", 'i');

                            if(re.test(line)){
                              // lines[l] = line.replace(re, "<span class = 'taint'>" + thisAddress + "</span>");
                              // lines[l] = "<span class = 'taint'>" + line + "</span>";

                              var color = colorScale(matched_addresses[k]);

                              

                              lines[l] = "<span style = 'background-color: " + color + " ; " 
                              + "color: white; "
                              + "'>" + escapeHtml(line) + "</span>";  // mcritweb: issue #69


                              break;
                            }


                          }
                        }

                        var textContent = lines.join("\n");
                        textContent = textContent.replace(/\n<\/span>/ ,"</span>");

                      }

                      
                      for(var i=0; i < textToHighlight.length; i++){
                        
                        // textToHighlight[i].classed("highlight", true);
                        // textToHighlight[i].text(textContent);
                        textToHighlight[i].node().innerHTML = textContent;

                        // replaceStr = textToHighlight[i].node().innerHTML;
                        // replaceStr = replaceStr.replace(/\n<\/span>/ ,"</span>");

                        // textToHighlight[i].node().innerHTML = replaceStr;
                      
                      }

                      

                    }
                  }




                }


              });

          }
        }

  });


  // This function cleans any previous highlights of any type
  function clearPrevHighlight(){

  }

  // This function highlights the sets of instructions returned by UERDetector code
  // The returned value is a JSON object with the following format:
  // { nodeId1:[[instruction address, location in the instruction ], [], ....],
  //   nodeId2: [[]], 
  //   ....
  //   nodeIdn:[[]]
  //  }



function highlightUERs(UERtype){

      // Remove previous highlights
      // Copy the results over to match_list

      // Remove previous highlights
        for(var i=0; i<matched_tspans_graphs.length; i++){

          // matched_tspans_graphs[i].classed("taint", false);  
          // matched_tspans_graphs[i].rect.classed("taint", false);

          matched_tspans_graphs[i].rect.style("fill", "none");
          matched_tspans_graphs[i].tspan.classed("taint", false);

        }

        matched_tspans_graphs = [];

        // Remove highlight on the trace
        // Use match_list  

        var textToHighlight = [];

          for (var nodeId in match_list) {
            if (match_list.hasOwnProperty(nodeId)) {

              if(isTraceSupplied){
                if(nodeId in nodeToTextGroups){
                  textToHighlight = nodeToTextGroups[nodeId];
                }
              }

              // if(textToHighlight.length > 0){
              //   // var replaceStr = textToHighlight[0].text();
              //   // var replaceStr = replaceStr.replace("<span class = 'taint'>", '');
              //   // replaceStr = replaceStr.replace("</span>", '');

              //   var replaceStr = textToHighlight[0].node().innerHTML;
              //   // replaceStr = replaceStr.replace(/<span[\s\S]*['"]>/gi ,"");
              //   // replaceStr = replaceStr.replace(/<span[\s\S]*>/gi ,"");

              //   replaceStr = replaceStr.replace(/<span class = ['"]taint['"]>/gi ,"");
                
              //   replaceStr = replaceStr.replace(/\n<\/span>/gi ,"");
              //   replaceStr = replaceStr.replace(/<\/span>/gi ,"");

              // }

              if(textToHighlight.length > 0){
                var replaceStr = textToHighlight[0].text();
              }

              for(var i = 0; i<textToHighlight.length; i++){
                // textToHighlight[i].text(replaceStr);
                // textToHighlight[i].node().innerHTML = replaceStr;

                textToHighlight[i].text(replaceStr);

              }

            }
          }

          match_list = {};


            //UER Detection is performed using dotfiles
            // send the dotfile to the service

            //Construct the request object
            var UERRequest = {dotfile: dotFile, UERtype: UERtype};

            // Send request for UER information
            d3.xhr("../getUERs/")
            .header("Content-Type", "application/json")
            .post(JSON.stringify(UERRequest),
              function(err, result){
                // console.log("Response: ", result.responseText);

                UEROutput = JSON.parse(result.responseText);
                // console.log(UEROutput);
                // match_list = UEROutput;

                  // For the nodes in the result, search through all the tspan elements
                  // If the tspan contains a matching address, then highlight it and add it to the list of modified tspans,
                  // Lookup the corresponding blocks in the trace
                  
                  // d3.selectAll("#graphContainer g.nodes tspan").each()

                  matched_tspans_graphs = [];
                  // match_list = {};
                  match_list = UEROutput;


                  // Apply highligting and store the highlighted tspans
                  for (var nodeId in UEROutput) {
                    if (UEROutput.hasOwnProperty(nodeId)) {
                        
                      // We have the nodeId and we have the corresponding blocks in the traces 

                      var matched_addresses = UEROutput[nodeId];

                      // var thisbbox = nodesAll[nodeId].select("text").node().getBBox();
                      
                      var hasFunctionName = false;
                      if(nodesAll[nodeId].select("tspan").text().trim() != ""){
                        hasFunctionName = true;
                      }

                      //Find them in the tspans
                      nodesAll[nodeId].selectAll("tspan").each(function(d, i){
                        
                        var text = d3.select(this).text();

                        // Check the matched addresses
                        // This is an array of array.
                        // The inner array has two elements:
                        // First element is the instruction address
                        // Second element is the portion of instruction that is UER

                        for(var k = 0; k<matched_addresses.length; k++){
                          var re = new RegExp(matched_addresses[k][0], 'i');
                          
                          if(re.test(text)){
                            re = new RegExp(escapeRegExp(matched_addresses[k][1]), 'i');

                            // Highlight it
                            // Add it to the list

                            // d3.select(this).style("fill", "white");
                            
                            // d3.select(this).classed("taint", true);
                            
                            // d3.select(this).style("fill", colorScale(k));
                            
                            var y = (i-1)*13 + 1;

                            if(hasFunctionName){
                              y = (i)*13 + 1;  
                            }

                            // Modify the width and x-coordinate of the bbox based on the starting index of text-match
                            // in the instruction and the length of the text
                            // Use matched_addresses[k][1] as the text
                            
                            var unitWidth=8;
                            var findIndex =  text.search(re);
                            var x = findIndex*unitWidth-3;

                            var height = 14;
                            var width = (matched_addresses[k][1].length)*unitWidth;

                            var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
                            rect.setAttribute("x", x);
                            rect.setAttribute("y", y);
                            rect.setAttribute("width", width);
                            rect.setAttribute("height", height);

                            // rect = nodesAll[nodeId].node().insertBefore(rect, this);
                            rect = nodesAll[nodeId].select("g").node().insertBefore(rect, nodesAll[nodeId].select("text").node());

                            rect = d3.select(rect);
                            rect.style("fill", "#d1b5e5")
                            .style("stroke", "none");

                            // rect.classed("taint", true);

                            // var rect = nodesAll[nodeId].select("g").append("rect")
                            //  .attr("x", x)
                            //  .attr("y", y)
                            //  .attr("width", width)
                            //  .attr("height", height)
                            //  .classed("taint", true);

                            matched_tspans_graphs.push({rect: rect, tspan: d3.select(this)});
                              // matched_tspans_graphs.push(d3.select(this));
  
                            // break;
                          
                          }

                        }


                      });

                      var textToHighlight = [];

                      // Highlight the trace text
                      
                      // Go through the lines;
                      // If the line contains a matching address, Put a span element in the line
                      // i.e Replace the text with <span>matched_portion</span>
                      // Prepare the array and join the array using "\n"

                      // Trace Backtaint Highlighting
                      if(isTraceSupplied){
                        if(nodeId in nodeToTextGroups){
                          textToHighlight = nodeToTextGroups[nodeId];
                        }
                      } 

                      // Previous highlighting removed at the start

                      // Start with any textblock and prepare the text to replace
                      // Then replace it in all instances
                      if(textToHighlight.length > 0){

                        var lines = textToHighlight[0].text().split('\n');

                        for(var l=0; l<lines.length; l++){
                          var line = lines[l];
                          // mcritweb: issue #69 - escaped once here rather than in the
                          // loop below, which appends a span per match and would
                          // otherwise escape the markup left by the previous pass.
                          lines[l] = escapeHtml(lines[l]);

                          for(var k=0; k<matched_addresses.length; k++){
                            var thisAddress = matched_addresses[k][0];
                            // var re = new RegExp("\\b" + thisAddress + "\\b", 'i');
                            var re = new RegExp(thisAddress, 'i');

                            if(re.test(line)){

                              // if((nodeId == "B13") && l==9){
                              //   debugger;
                              // }

                              // re = new RegExp("\\b" + escapeRegExp(matched_addresses[k][1]) + "\\b", 'i');
                              // mcritweb: issue #69 - needle escaped to match the
                              // escaped line above; `line` stays raw for the test.
                              re = new RegExp(escapeRegExp(escapeHtml(matched_addresses[k][1])), 'i');

                              lines[l] = lines[l].replace(re, "<span class = 'taint'>" + escapeHtml(matched_addresses[k][1]) + "</span>");
                              // lines[l] = "<span class = 'taint'>" + line + "</span>";

                              // var color = "#8856a7";

                              // lines[l] = "<span style = 'background-color: " + color + " ; " 
                              // + "color: white; "
                              // + "'>" + line + "</span>";

                              // break;
                            }

                          }
                        }

                        var textContent = lines.join("\n");
                        textContent = textContent.replace(/\n<\/span>/ ,"</span>");

                      }

                      
                      for(var i=0; i < textToHighlight.length; i++){
                        
                        // textToHighlight[i].classed("highlight", true);
                        // textToHighlight[i].text(textContent);
                        textToHighlight[i].node().innerHTML = textContent;

                        // replaceStr = textToHighlight[i].node().innerHTML;
                        // replaceStr = replaceStr.replace(/\n<\/span>/ ,"</span>");

                        // textToHighlight[i].node().innerHTML = replaceStr;
                      
                      }

                      

                    }
                  }


              });

}



  function updateTaint(num_disp_addr) {

    console.log("Value is " + num_disp_addr);

    if(taintOutputList.length>0){

            // Remove previous taints
        for(var i=0; i<matched_tspans_graphs.length; i++){

          // matched_tspans_graphs[i].classed("taint", false);  
          
          // matched_tspans_graphs[i].rect.classed("taint", false);
          matched_tspans_graphs[i].rect.style("fill", "none");
          
          matched_tspans_graphs[i].tspan.classed("taint", false);

        }

        matched_tspans_graphs = [];

        // Remove taint on the trace
        // Use match_list  

        var textToHighlight = [];

        for (var nodeId in match_list) {
          if (match_list.hasOwnProperty(nodeId)) {

            if(isTraceSupplied){
              if(nodeId in nodeToTextGroups){
                textToHighlight = nodeToTextGroups[nodeId];
              }
            }

            // if(textToHighlight.length > 0){
            //   // var replaceStr = textToHighlight[0].text();
            //   // var replaceStr = replaceStr.replace("<span class = 'taint'>", '');
            //   // replaceStr = replaceStr.replace("</span>", '');

            //   var replaceStr = textToHighlight[0].node().innerHTML;
            //   replaceStr = replaceStr.replace(/<span[\s\S]*['"]>/ ,"");
            //   replaceStr = replaceStr.replace(/\n<\/span>/ ,"");

            // }

            if(textToHighlight.length > 0){
                var replaceStr = textToHighlight[0].text();
            }

            for(var i = 0; i<textToHighlight.length; i++){
              textToHighlight[i].text(replaceStr);
            }

          }
        }

        match_list = {};

        console.log("Cleared everything");

      var colorScale = d3.scale.linear()
              .domain([0, num_disp_addr - 1])
              
              // .range(["#8856a7", "#efedf5"])
              // Make the color scale darker so that white text works
              .range(["#8856a7", "#c0a5d1"])
              .interpolate(d3.interpolateHcl);


              // Go through the original graph, make a list of all the nodes with
              // all the matching addresses contained in it,
              // For the matching nodes, search through all the tspan elements
              // If the tspan contains a matching address, then highlight it and add it to the list of modified tspans,
              // Lookup the corresponding blocks in the trace
              // Clear any existing spans (by giving them a class if the gradient is not needed),
              // and then apply span on new elements.

              // d3.selectAll("#graphContainer g.nodes tspan").each()

              var graph_nodes = g.nodes();
              match_list = {};
              matched_tspans_graphs = [];

              //Since an address only occurs in a CFG once, once an address is matched in some node, it 
              // need not be considered in any other node.
              // Here a boolean array the size of original address array keeps track of matched addresses
              // A duplicate array with addresses sorted alphabetically is used so that earlier addresses are checked first
              // If the address space is different, then in addition to alphabetical sorting it also needs to take into account variable length
              // But within the same node, the order of addresses is probably in sorted order with just alphabetical sort
              // if library code and regular code is not interleaved in the same node
              
              // An alternative way to creating a boolean array to track matched addresses is to delete elements from the duplicate array and that keeps on 
              // decreasing the number of array accesses. Array should be traversed in reverse order if we want to use splice to delete elments from an array and
              // still go through all the elements of an array.

              var is_matched_index = new Array(num_disp_addr).fill(false);
              
              // Don't sort the addresses //
              // Use the original list and use the order in it //
              // var sorted_addresses = taintOutputList.slice(0).sort();
              var sorted_addresses = taintOutputList.slice(0,num_disp_addr);
              

              var matched_count = num_disp_addr;
              // Since we work with sorted addresses, the addresses will be sorted in the match_list as well.

              for(var i = 0; i<graph_nodes.length; i++){
              
                  var nodeId = graph_nodes[i];
                  var node = g.node(nodeId);
                  var label = node.label;
                
                  for(var k=0; k<is_matched_index.length; k++){

                    if(is_matched_index[k]) {
                      continue;
                    }
                    var re = new RegExp(sorted_addresses[k], 'i');
                    // var re = new RegExp("\\b" + sorted_addresses[k] + "\\b", 'i');

                    if(re.test(label)){

                      console.log("match");

                      if(match_list[nodeId] == null){
                        match_list[nodeId] = [];
                      }
                      match_list[nodeId].push(k);
                      // If using the alternative approach, need to store the address itself since index 
                      // keeps on changing on every iteration of outer loop.

                      is_matched_index[k] = true;
                      matched_count--;

                      console.log(matched_count);

                    }  


                  }

                  // If using alternative approach, need to delete matched nodes here; Use splice to remove elements without
                  // creating any gaps

                  if(matched_count==0){
                    break;
                  }

              }

              console.log(match_list);
              console.log(taintOutputList);
              console.log(sorted_addresses);

              // Match list computed; Now apply highligting and store them
              // Remove highlighting on previous matches
              for (var nodeId in match_list) {
                if (match_list.hasOwnProperty(nodeId)) {
                    
                  // We have the nodeId and we have the corresponding blocks in the traces 

                  var matched_addresses = match_list[nodeId];

                  var thisbbox = nodesAll[nodeId].select("text").node().getBBox();
                  var hasFunctionName = false;
                  if(nodesAll[nodeId].select("tspan").text().trim() != ""){
                    hasFunctionName = true;
                  }

                  //Find them in the tspans
                  nodesAll[nodeId].selectAll("tspan").each(function(d, i){
                    
                    var text = d3.select(this).text();

                    // Check the matched addresses
                    for(var k = 0; k<matched_addresses.length; k++){
                      var re = new RegExp(sorted_addresses[matched_addresses[k]], 'i');
                      // var re = new RegExp("\\b" + sorted_addresses[matched_addresses[k]] + "\\b", 'i');

                      if(re.test(text)){
                        // Highlight it
                        // Add it to the list

                        // d3.select(this).style("fill", "white");
                        d3.select(this).classed("taint", true);

                        // d3.select(this).style("fill", colorScale(k));
                        

                        // var thisbbox = this.getBBox();
                        

                        // var y = thisClientRect.height*(i-1);
                        
                        var y = (i-1)*13 + 1;

                        if(hasFunctionName){
                          y = (i)*13 + 1;  
                        }

                        var x = 0;
                        var height = 14;
                        var width = thisbbox.width;

                        var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
                        rect.setAttribute("x", x);
                        rect.setAttribute("y", y);
                        rect.setAttribute("width", width);
                        rect.setAttribute("height", height);


                        // rect = nodesAll[nodeId].node().insertBefore(rect, this);
                        rect = nodesAll[nodeId].select("g").node().insertBefore(rect, nodesAll[nodeId].select("text").node());

                        rect = d3.select(rect);
                        rect.style("fill", colorScale(matched_addresses[k]))
                        .style("stroke", "none");

                        // rect.classed("taint", true);

                        // var rect = nodesAll[nodeId].select("g").append("rect")
                        //  .attr("x", x)
                        //  .attr("y", y)
                        //  .attr("width", width)
                        //  .attr("height", height)
                        //  .classed("taint", true);

                        matched_tspans_graphs.push({rect: rect, tspan: d3.select(this)});
                          // matched_tspans_graphs.push(d3.select(this));

                          console.log("Taint Highlighted on graph");

                        break;
                      
                      }

                    }


                  });


                  

                  var textToHighlight = [];

                  // Highlight the trace text
                  
                  // Go through the lines;
                  // If the line contains a matching address, Put a span element in the line
                  // i.e Replace the text with <span>line</span>
                  // Prepare the array and join the array using "\n"

                  // Trace Backtaint Highlighting
                  if(isTraceSupplied){
                    if(nodeId in nodeToTextGroups){
                      textToHighlight = nodeToTextGroups[nodeId];
                    }
                  } 

                  // Previous highlighting removed at the start

                  // Start with any textblock and prepare the text to replace
                  // Then replace it in all instances
                  if(textToHighlight.length > 0){
                    var lines = textToHighlight[0].text().split('\n');

                    for(var l=0; l<lines.length; l++){
                      var line = lines[l];
                      for(var k=0; k<matched_addresses.length; k++){
                        var thisAddress = sorted_addresses[matched_addresses[k]];
                        var re = new RegExp("\\b" + thisAddress + "\\b", 'i');

                        if(re.test(line)){
                          // lines[l] = line.replace(re, "<span class = 'taint'>" + thisAddress + "</span>");
                          // lines[l] = "<span class = 'taint'>" + line + "</span>";

                          var color = colorScale(matched_addresses[k]);

                          

                          lines[l] = "<span style = 'background-color: " + color + " ; " 
                          + "color: white; "
                          + "'>" + escapeHtml(line) + "</span>";  // mcritweb: issue #69


                          break;
                        }


                      }
                    }

                    var textContent = lines.join("\n");
                    textContent = textContent.replace(/\n<\/span>/ ,"</span>");

                  }

                  
                  for(var i=0; i < textToHighlight.length; i++){
                    
                    // textToHighlight[i].classed("highlight", true);
                    // textToHighlight[i].text(textContent);
                    textToHighlight[i].node().innerHTML = textContent;

                    // replaceStr = textToHighlight[i].node().innerHTML;
                    // replaceStr = replaceStr.replace(/\n<\/span>/ ,"</span>");

                    // textToHighlight[i].node().innerHTML = replaceStr;
                  
                  }

                  console.log("Taint Highlighted on trace");

                  

                }
              }

            

    }
}



  d3.select("#myTaintSlider").on("input", function(){
    var value = this.value;
    d3.select("#sliderOutput").text(value);
  
    updateTaint(value);
    
  
  });


  d3.select("#loadFile")
        .on("click", function(){
          loadFile();
        }
      );

  // mcritweb: issue #69 - reads a /explore/findLoops/ response. Loop detection
  // needs a single entry node and reports an error for a CFG that has more than
  // one, so a failure here is expected rather than exceptional; it must leave the
  // panel without loops instead of aborting the render that follows it.
  function parseLoops(err, result){
    if(err || !result){
      console.warn("loop detection request failed", err);
      return [];
    }
    var parsed;
    try {
      parsed = JSON.parse(result.responseText);
    } catch(e) {
      console.warn("loop detection returned no usable result", e);
      return [];
    }
    return Array.isArray(parsed) ? parsed : [];
  }

    function loadWithDotGraphAndFunctionIdA(function_id, node_colors) {
      isTraceSupplied = false;
      // Send request for loop information; load when ready
        d3.xhr(window.location.origin + "/explore/fetchDotGraph/" + function_id).header("Content-Type", "text/plain")
        .get(function(err, result){
          dot_graph = result.responseText;
          dotFile_a = dot_graph.replace(/\\l/g, "\n");
          g_a = graphlibDot.parse(dotFile_a);
          cfgPanels.a.graph = g_a;  // mcritweb: issue #69
          // Send request for loop information; load when ready
          d3.xhr(window.location.origin + "/explore/findLoops/")
            .header("X-CSRFToken", csrfToken())  // mcritweb: issue #83
            .header("Content-Type", "text/plain")
            .post(dotFile_a,
              function(err, result){
                // console.log("Response: ", result.responseText);
                // mcritweb: issue #69 - each panel keeps its own loops, and the
                // graph is drawn even when loop detection could not answer.
                cfgPanels.a.loops = parseLoops(err, result);

                // loopify_dagre.init();
                var modifiedDotFile_a = loopify_dagre.modifiedDotFile;
                var modifiedDotFile_a = dotFile_a;
                // console.log(modifiedDotFile);
                graph_to_display_a = graphlibDot.parse(modifiedDotFile_a);
                cfgPanels.a.rendered = graph_to_display_a;  // mcritweb: issue #69
      
      
                showGraph("a", node_colors);
                // mcritweb: issue #69 - the two panels load independently, so a
                // highlight switched on while this one was still in flight has
                // already painted the other and would never reach these nodes.
                reapplyHighlight();
                renderLoopBoundaries("a");  // mcritweb: issue #69, was loopify_dagre.addBackground()

                fnManip.init();
                // loopCollapser.init();
              });
      });
  
  
      d3.select("#loading").classed("hidden", true);
    }

  function loadWithDotGraphAndFunctionIdB(function_id, node_colors) {
    isTraceSupplied = false;
    // Send request for loop information; load when ready
     d3.xhr(window.location.origin + "/explore/fetchDotGraph/" + function_id).header("Content-Type", "text/plain")
      .get(function(err, result){
        dot_graph = result.responseText;
        dotFile_b = dot_graph.replace(/\\l/g, "\n");
        g_b = graphlibDot.parse(dotFile_b);
        cfgPanels.b.graph = g_b;  // mcritweb: issue #69
        // Send request for loop information; load when ready
        d3.xhr(window.location.origin + "/explore/findLoops/")
          .header("X-CSRFToken", csrfToken())  // mcritweb: issue #83
          .header("Content-Type", "text/plain")
          .post(dotFile_b,
            function(err, result){
              // console.log("Response: ", result.responseText);
              // mcritweb: issue #69 - each panel keeps its own loops, and the
              // graph is drawn even when loop detection could not answer.
              cfgPanels.b.loops = parseLoops(err, result);

              // loopify_dagre.init();
              var modifiedDotFile_b = loopify_dagre.modifiedDotFile;
              var modifiedDotFile_b = dotFile_b;
              // console.log(modifiedDotFile);
              graph_to_display_b = graphlibDot.parse(modifiedDotFile_b);
              cfgPanels.b.rendered = graph_to_display_b;  // mcritweb: issue #69
    
    
              showGraph("b", node_colors);
              // mcritweb: issue #69 - see the "a" loader.
              reapplyHighlight();
              renderLoopBoundaries("b");  // mcritweb: issue #69, see the "a" loader

              fnManip.init();
              // loopCollapser.init();
            });
    });


    d3.select("#loading").classed("hidden", true);
  }

  // populate the dicts with nodes, edges, and edgelabels
  function fillNodesandEdgesA(node_colors){

    var container_id = "#graphContainer_a"

    // mcritweb: issue #69 - everything below writes into nodesAll/edgesAll/
    // edgeLabelsAll, so point those at this panel first.
    var panel = usePanel(cfgPanels.a);

    // compute the degrees of the node i.e. sum of indegrees or sum of outdegrees
    var re = /ct:(\d+)/i;
    var max_deg = 1, max_ct = 1;
    
    var graph_nodes = g_a.nodes();
    for(var i=0; i<graph_nodes.length; i++){
      var nodeId = graph_nodes[i];
      //Work with only the basic nodes not the function nodes
      if(g_a.children(nodeId).length == 0){
        //This is a basic node; Compute the degree
        var outEdges = g_a.outEdges(nodeId);
        var deg = 0;

        for(var j=0; j<outEdges.length; j++){
          // get the count from the edges and sum them 
          // store the degree in the graph's node object
          var label = g_a.edge(outEdges[j]).label;
          // Check if count is present  
          if(re.test(label)){
            var temp_ct = parseInt(label.match(re)[1]); 
            if(temp_ct > max_ct){
              max_ct = temp_ct;
            }
            deg += temp_ct;
          }
        }

        if(deg>max_deg){
          max_deg = deg;
        }
        g_a.node(nodeId)["degree"] = deg; 
      }
    }
    
    degreeScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range(["#faf0e6", "#ff7f00"])
      .interpolate(d3.interpolateHcl);

    degreeBorderFillScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range(["#f78a62", "#ad2e00"])
      .interpolate(d3.interpolateHcl);

    degreeBorderScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range([3, 15]);

    var edgeCountScale = d3.scale.linear()
      .domain([0, Math.log10(max_ct)])
      .range([1.5, 6]);

    d3.selectAll(container_id + " " + "g.node.enter")
  		.each(function(d) { 
  			nodesAll[d] = d3.select(this);
        if(g_a.node(d).style=="dashed") {
            nodesAll[d].classed("dashed", true);
          }

          if("degree" in g_a.node(d)){
            var deg = g_a.node(d).degree;

            // Change this fill property to stroke property
            // MCRIT this could possibly allow us to paint the nodes?
            // nodesAll[d].select("rect").style("fill", degreeScale(Math.log10(deg)));
            if (d in node_colors["a"]) {
              nodesAll[d].select("rect").style("fill", node_colors["a"][d]);
              panel.colors[d] = node_colors["a"][d];  // mcritweb: issue #69
            }

            // Change this stroke-width property to stroke property i.e. border fill instead of background fill
            nodesAll[d].select("rect").style("stroke-width", degreeBorderScale(Math.log10(deg)));

            // nodesAll[d].select("rect").style("stroke-width", 15);
            // nodesAll[d].select("rect").style("stroke", degreeBorderFillScale(Math.log10(deg)));

          }

  		 });	

  	// var def_stroke_width = parseFloat(d3.select("g.edgePath.enter").style("stroke-width"));

  	d3.selectAll(container_id + " " + "g.edgePath.enter")
  		.each(function(d){

  			if(!(g_a.hasEdge(d))){
          d3.select(this).remove(); 
          return;
        }

        edgesAll[d] = d3.select(this);

        if(g_a.edge(d).style=="dashed") {
            edgesAll[d].classed("dashed", true);
        }

        //Encode the edge count with edge width
        //get the edge count
        var label = g_a.edge(d).label;
          
        // Check if count is present  
        if(re.test(label)){
          var ct = parseInt(label.match(re)[1]);
          ct = Math.log10(ct);

          // if(ct<0) {ct = 0;}
          // ct = ct*def_stroke_width + def_stroke_width;
          // edgesAll[d].style("stroke-width", ct+"px");

          edgesAll[d].style("stroke-width", edgeCountScale(ct)+"px");

        }

  		});	

  	d3.selectAll(container_id + " " + "g.edgeLabel.enter")
  		.each(function(d){
        if(!(g_a.hasEdge(d))){
          d3.select(this).remove(); 
          return;
        }

  			edgeLabelsAll[d] = d3.select(this);
  		});	

  }

  // populate the dicts with nodes, edges, and edgelabels
  function fillNodesandEdgesB(node_colors){

    var container_id = "#graphContainer_b"

    // mcritweb: issue #69 - see fillNodesandEdgesA.
    var panel = usePanel(cfgPanels.b);

    // compute the degrees of the node i.e. sum of indegrees or sum of outdegrees
    var re = /ct:(\d+)/i;
    var max_deg = 1, max_ct = 1;
    
    var graph_nodes = g_b.nodes();
    for(var i=0; i<graph_nodes.length; i++){
      var nodeId = graph_nodes[i];
      //Work with only the basic nodes not the function nodes
      if(g_b.children(nodeId).length == 0){
        //This is a basic node; Compute the degree
        var outEdges = g_b.outEdges(nodeId);
        var deg = 0;

        for(var j=0; j<outEdges.length; j++){
          // get the count from the edges and sum them 
          // store the degree in the graph's node object
          var label = g_b.edge(outEdges[j]).label;
          // Check if count is present  
          if(re.test(label)){
            var temp_ct = parseInt(label.match(re)[1]); 
            if(temp_ct > max_ct){
              max_ct = temp_ct;
            }
            deg += temp_ct;
          }
        }

        if(deg>max_deg){
          max_deg = deg;
        }
        g_b.node(nodeId)["degree"] = deg; 
      }
    }
    
    degreeScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range(["#faf0e6", "#ff7f00"])
      .interpolate(d3.interpolateHcl);

    degreeBorderFillScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range(["#f78a62", "#ad2e00"])
      .interpolate(d3.interpolateHcl);

    degreeBorderScale = d3.scale.linear()
      .domain([0,Math.log10(max_deg)])
      .range([3, 15]);

    var edgeCountScale = d3.scale.linear()
      .domain([0, Math.log10(max_ct)])
      .range([1.5, 6]);

    d3.selectAll(container_id + " " + "g.node.enter")
  		.each(function(d) { 
  			nodesAll[d] = d3.select(this);
        if(g_b.node(d).style=="dashed") {
            nodesAll[d].classed("dashed", true);
          }

          if("degree" in g_b.node(d)){
            var deg = g_b.node(d).degree;

            // Change this fill property to stroke property
            // MCRIT this could possibly allow us to paint the nodes?
            // nodesAll[d].select("rect").style("fill", degreeScale(Math.log10(deg)));
            if (d in node_colors["b"]) {
              nodesAll[d].select("rect").style("fill", node_colors["b"][d]);
              panel.colors[d] = node_colors["b"][d];  // mcritweb: issue #69
            }
            // Change this stroke-width property to stroke property i.e. border fill instead of background fill
            nodesAll[d].select("rect").style("stroke-width", degreeBorderScale(Math.log10(deg)));

            // nodesAll[d].select("rect").style("stroke-width", 15);
            // nodesAll[d].select("rect").style("stroke", degreeBorderFillScale(Math.log10(deg)));

          }

  		 });	

  	// var def_stroke_width = parseFloat(d3.select("g.edgePath.enter").style("stroke-width"));

  	d3.selectAll(container_id + " " + "g.edgePath.enter")
  		.each(function(d){

  			if(!(g_b.hasEdge(d))){
          d3.select(this).remove(); 
          return;
        }

        edgesAll[d] = d3.select(this);

        if(g_b.edge(d).style=="dashed") {
            edgesAll[d].classed("dashed", true);
        }

        //Encode the edge count with edge width
        //get the edge count
        var label = g_b.edge(d).label;
          
        // Check if count is present  
        if(re.test(label)){
          var ct = parseInt(label.match(re)[1]);
          ct = Math.log10(ct);

          // if(ct<0) {ct = 0;}
          // ct = ct*def_stroke_width + def_stroke_width;
          // edgesAll[d].style("stroke-width", ct+"px");

          edgesAll[d].style("stroke-width", edgeCountScale(ct)+"px");

        }

  		});	

  	d3.selectAll(container_id + " " + "g.edgeLabel.enter")
  		.each(function(d){
        if(!(g_b.hasEdge(d))){
          d3.select(this).remove(); 
          return;
        }

  			edgeLabelsAll[d] = d3.select(this);
  		});	

  }

  // draws an edge between the nodes u & v; 
  // updates the path which is provided as input
  function drawEdge(u, v, edge){

    var path = edge.select("path");
    var rect1 = u.select("rect");
    var uTransformText = u.attr("transform");
    var uTranslate = d3.transform(uTransformText).translate;  //returns [tx,ty]

    var rect2 = v.select("rect");
    var vTransformText = v.attr("transform");
    var vTranslate = d3.transform(vTransformText).translate;  //returns [tx,ty]

    var x1 = Number(rect1.attr("x")) + uTranslate[0];
    var y1 = Number(rect1.attr("y")) + uTranslate[1];
    var ht1 = Number(rect1.attr("height"));
    var width1 = Number(rect1.attr("width"));
  
    var x2 = Number(rect2.attr("x")) + vTranslate[0];
    var y2 = Number(rect2.attr("y")) + vTranslate[1];
    var ht2 = Number(rect2.attr("height"));
    var width2 = Number(rect2.attr("width"));

    var x1new, x2new, y1new, y2new;
                
    if((y1+ht1) < y2) {
      y1new = y1+ht1;
      x1new = x1 + width1/2.0;
      x2new = x2 + width2/2.0;
      y2new = y2;
    } else if (y1 > (y2+ht2)){
      y1new = y1;
      x1new = x1 + width1/2.0;
      y2new = y2 + ht2;
      x2new = x2 + width2/2.0;
    } else if (x1 > (x2+width2)) {
      y1new = y1 + ht1/2.0;
      x1new = x1;
      y2new = y2+ht2/2.0;
      x2new = x2 + width2;
    } else {

      y1new = y1+ht1/2.0;
      x1new = x1 + width1;
      y2new = y2+ht2/2.0;
      x2new = x2;  
    }           

    //Adjust it to take the translation of the edge into account
    var edgeTranslate = d3.transform(edge.attr("transform")).translate;
    x1new = x1new - edgeTranslate[0];
    y1new = y1new - edgeTranslate[1];
    x2new = x2new - edgeTranslate[0];
    y2new = y2new - edgeTranslate[1];

    // handle the self edges
    if (u.datum()===v.datum()){
      // console.log("Self loop point reached");
      path.attr("d", createSelfLoop(x1new, y1new));
    } else {
      path.attr("d", "M " + x1new + " , " + y1new + 
      " L " + x2new + " , " + y2new );
    }
  }

  function createSelfLoop(x, y){
    // console.log("Generating self loop");
    // return "M " + x + ", " + y + " L " + (x-10) + ", " + (y-4) + " C " + (x-20) 
    // + ", " + (y-6) + ", " + (x-40) +", " + (y-15) + ", " + (x-49) + ", " 
    // + (y-8) +" C "+ (x-59) + ", " + (y-1) + ", " + (x-59) + ", " + (y+21)
    // + ", " + (x-59) + ", " + (y+43) + " C " + (x-59) + ", " + (y+65) + ", "
    // + (x-59) + ", " + (y+87) + ", " + (x-49) + ", " + (y-94) + " C " + (x-40) 
    // + ", " + (y+102) + ", " + (x-20) + ", " + (y+94) + ", " + (x-10) + ", " + 
    // y+90 + " L " + x + ", " + (y+87);

    return "M " + x + ", " + y + " L " + (x+50) +", " + (y+70)
    + " L " + (x+150) + ", " + (y+70) + " L " + (x+150) +", " 
    + (y-70) + " L " + (x+50) + ", " + (y-70) + " L " + x + ", " + y;

  }

  // computes the bounding box of a group of nodes
  // Input index is the id of the group
  // Returns a bounding box with left, top, right, and bottom attributes
  function computeBoundingBox(index) {

    var grpbbox = {left:null, right:null, top:null, bottom:null};

  	for(var i = 0; i < nodeGroups[index].length; i++){
  		var nodeId = nodeGroups[index][i];
  		var node = nodesAll[nodeId];

  		var transformText = node.attr("transform");
  		var translate = d3.transform(transformText).translate; //returns [tx,ty]

  		var rect = node.select("rect");

  		var x = Number(rect.attr("x")) + translate[0];
        var y = Number(rect.attr("y")) + translate[1];
        var height = Number(rect.attr("height"));
        var width = Number(rect.attr("width"));

        if(grpbbox.left === null || x < grpbbox.left) grpbbox.left = x;
        if (grpbbox.top === null || y < grpbbox.top) grpbbox.top = y;
        if(grpbbox.right === null || (x+width) > grpbbox.right) grpbbox.right = x+width;
        if(grpbbox.bottom === null || (y+height) > grpbbox.bottom) grpbbox.bottom = y+height;

  	}
    
    //code to test bounding box 
    // d3.select("#graphContainer g").append("rect")
    //   .attr("x", grpbbox.left)
    //   .attr("y", grpbbox.top)
    //   .attr("width", grpbbox.right - grpbbox. left)
    //   .attr("height", grpbbox.bottom - grpbbox.top)
    //   .attr("fill", "red");
  	return grpbbox;

  }

  // Inserts the node and edges between a group's meta node and its neighbors
  // index is the Id of the group
  // grpbbox is an object containing the left, top, right and bottom attributes
  function insertNodeAndEdgesofGrp(index, grpbbox){


  	var cx = (grpbbox.left + grpbbox.right)/2.0;
  	var cy = (grpbbox.top + grpbbox.bottom)/2.0;

  	var nodes = d3.select("#graphContainer g.nodes");
  	var node = nodes.append("g").attr("class","group node enter")
  		.attr("transform", "translate( " + cx + " , " + cy + " )")
  		.datum(index);

  	nodesAll[index] = node;
  	
    var rect = node.append("rect");

  	// TODO:add this node to graph g
  	// make a function 	
  		
  	
  	var innerg = node.append("g");
  	var text = innerg.append("text").attr("text-anchor", "left");
  	text.append("tspan").attr("dy", "1em").attr("x", 1)
  		.text(nodeGroupMeta[index]);

  	var textbbox = text.node().getBBox();	

    var rectbbox = {};
    rectbbox.width = textbbox.width;
    rectbbox.height = textbbox.height;

    if(rectbbox.width < 40) {
     rectbbox.width = 40;
    }
    if(rectbbox.height < 30)  {
      rectbbox.height  = 30;
    }

  	innerg.attr("transform", "translate( " + (-textbbox.width/2.0) + " , " + (-textbbox.height/2.0) + ")"); 

  	// Code to get the bounding box of text
  	// To produce an enclosing rect
 	//  var test = document.getElementById("test");
	// test.innerHTML = nodeGroupMeta[index];
	// // test.style.fontSize = fontSize;
	// var height = (test.clientHeight + 1) + "px";
	// var width = (test.clientWidth + 1) + "px";

	rect.attr("x", -(rectbbox.width/2.0 + 5))
		.attr("y", -(rectbbox.height/2.0 + 5))
		.attr("width", rectbbox.width + 10)
		.attr("height", rectbbox.height + 10)
		.attr("rx", 5)
		.attr("ry", 5);
		
	// find and draw all edges connecting nodes inside groups with nodes outside 	
  
	var grpPredecessors = [];
	var grpSuccessors = [];

  for(var i = 0; i < nodeGroups[index].length; i++){
  		var nodeId = nodeGroups[index][i];
	
  		var predecessors = g.predecessors(nodeId);
      var successors = g.successors(nodeId);

      for(var j=0; j<predecessors.length; j++) {

        	//if not part of the grp, add it in the grpPredecessors list
        	var isPartOfGrp = false;
        	for(var k=0; k<nodeGroups[index].length; k++) {
        		
        		if(predecessors[j] === nodeGroups[index][k]) {
        			isPartOfGrp = true;		
        		}
        	}
        	if(!isPartOfGrp) {
        		//if not already in grppreds, add it
        		var isAdded = false;
        		for(var l=0; l<grpPredecessors.length; l++)	{
        			if(predecessors[j] === grpPredecessors[l])	{
        				isAdded = true;
        			}
        		}

        		if(!isAdded)	{
        			grpPredecessors.push(predecessors[j]);
        		}
        	}

        }

      for(var j=0; j<successors.length; j++) {

        	//if not part of the grp, add it in the grpSuccessors list
        	var isPartOfGrp = false;
        	for(var k=0; k<nodeGroups[index].length; k++) {
        		
        		if(successors[j] === nodeGroups[index][k]) {
        			isPartOfGrp = true;		
        		}
        	}
        	if(!isPartOfGrp) {
        		//if not already in grpsuccs, add it
        		var isAdded = false;
        		for(var l=0; l<grpSuccessors.length; l++)	{
        			if(successors[j] === grpSuccessors[l])	{
        				isAdded = true;
        			}
        		}

        		if(!isAdded)	{
        			grpSuccessors.push(successors[j]);
        		}
        	}

        }
        

  }
    nodeGroupsArray[index]["predecessors"] = [];
    nodeGroupsArray[index]["edges"] = [];
    nodeGroupsArray[index]["successors"] = [];

  	for(var i=0; i< grpPredecessors.length; i++)	{

  		var edges = d3.select("#graphContainer g.edgePaths");
  		var edgeId = "g" + index + "pe" + i;

      var edge = edges.append("g").attr("class","group edgePath enter")
  		.datum(edgeId);

  		var path = edge.append("path").attr("marker-end", "url(#arrowhead)");
  		
      // Adds this to the list of edges with id such as g0e1
  		edgesAll[edgeId] = edge;

      nodeGroupsArray[index]["predecessors"].push(grpPredecessors[i]);
      nodeGroupsArray[index]["edges"].push(edgeId);
  	
      drawEdge(nodesAll[grpPredecessors[i]], nodesAll[index], edge);

      // TODO:add this edge to graph g
  		// make a function
      // add edgeLabel; need to aggregate the counts


  	}
  	for(var i=0; i<grpSuccessors.length; i++)	{

      var edges = d3.select("#graphContainer g.edgePaths");
      var edgeId = "g" + index + "se" + i;

      var edge = edges.append("g").attr("class","group edgePath enter")
      .datum(edgeId);

      var path = edge.append("path").attr("marker-end", "url(#arrowhead)");
        
      // Adds this to the list of edges with id such as g0e1
      edgesAll[edgeId] = edge;
    
      nodeGroupsArray[index]["successors"].push(grpSuccessors[i]);
      nodeGroupsArray[index]["edges"].push(edgeId);

      drawEdge(nodesAll[index], nodesAll[grpSuccessors[i]], edge);

  		// TODO: add this edge to graph g
      // make a function
      // add edgeLabel; need to aggregate the counts

  	}
    
  }

  // hides the node and edges of the group of nodes
  // index identifies the group
  function hideNodeAndEdgesofGrp(index){

    nodesAll[index].style("display", "none");
    for(var i = 0; i < nodeGroupsArray[index]["edges"].length; i++) {
      edgesAll[nodeGroupsArray[index]["edges"][i]].style("display", "none");
    }

  }

  // updateEdges when the node is moved
  function updateEdges(thisNode) {
        var nodeId = thisNode.datum();
        var tempNodeId;
        rect = thisNode.select("rect");
        // var inEdges = g.inEdges(nodeId);
        // var outEdges = g.outEdges(nodeId);
        
        var predecessors = g.predecessors(nodeId);
        var successors = g.successors(nodeId);

        // g.setNode(1);
        // g.setNode(2, "lb2");
        // g.setEdge("t1", "t2", "eb1");
        // console.log(g.node(nodeId));
        // console.log(g.edge({v:nodeId, w:successors[0]}));

        
            if(predecessors.length != 0)  {
              for(var i=0; i<predecessors.length; i++){
                
                // get the edge between the nodes
                // get the x, y, ht, width of object & predecessor
                // update the attribute of path with new start and end coordinates
                
                var edge_lbl = g.inEdges(nodeId, predecessors[i])[0];
                
                var edge = edgesAll[edge_lbl];
                var path = edge.select("path"); 
                
                var transformText = thisNode.attr("transform");
                var translate = d3.transform(transformText).translate;  //returns [0,-25]
                
                var x2 = Number(rect.attr("x")) + translate[0];
                var y2 = Number(rect.attr("y")) + translate[1];
                var ht2 = Number(rect.attr("height"));
                var width2 = Number(rect.attr("width"));

                var rect2, x1, y1, ht1, width1, translate2;

                rect2 = nodesAll[predecessors[i]].select("rect");
                tempNodeId = predecessors[i];
                var transformText = nodesAll[predecessors[i]].attr("transform");
                translate2 = d3.transform(transformText).translate;
                
                x1 = Number(rect2.attr("x")) + translate2[0];
                y1 = Number(rect2.attr("y")) + translate2[1];
                ht1 = Number(rect2.attr("height"));
                width1 = Number(rect2.attr("width"));  

                var x1new, x2new, y1new, y2new;           

                if((y1+ht1) < y2) {
                  y1new = y1+ht1;
                  x1new = x1 + width1/2.0;
                  x2new = x2 + width2/2.0;
                  y2new = y2;
                } else if (y1 > (y2+ht2)){
                  y1new = y1;
                  x1new = x1 + width1/2.0;
                  y2new = y2 + ht2;
                  x2new = x2 + width2/2.0;
                } else if (x1 > (x2+width2)) {
                  y1new = y1 + ht1/2.0;
                  x1new = x1;
                  y2new = y2+ht2/2.0;
                  x2new = x2 + width2;
                } else {

                  y1new = y1+ht1/2.0;
                  x1new = x1 + width1;
                  y2new = y2+ht2/2.0;
                  x2new = x2;  
                }           

                var cx = (x1new+x2new)/2.0;
                var cy = (y1new+y2new)/2.0;

                //Adjust it to take the translation of the edge into account
                var edgeTranslate = d3.transform(edge.attr("transform")).translate;
                x1new = x1new - edgeTranslate[0];
                y1new = y1new - edgeTranslate[1];
                x2new = x2new - edgeTranslate[0];
                y2new = y2new - edgeTranslate[1];

                // handle the self edges
                if (nodeId===tempNodeId){
                  // console.log("Self loop point reached");
                  path.attr("d", createSelfLoop(x1new, y1new));
                } else {
                    path.attr("d", "M " + x1new + " , " + y1new + 
                    " L " + x2new + " , " + y2new );
                }

                //Update the edgeLabel if there is one
                if(edgeLabelsAll[edge_lbl] == undefined || edgeLabelsAll[edge_lbl] == null){
                  //No edge label
                } else {
                  var edgeLabel = edgeLabelsAll[edge_lbl];
                  // Translate it to the midpoint
                  edgeLabel.attr("transform", "translate(" + cx +  ", " + cy + ")");
                }
                
              }
            }

            if(successors.length != 0)  {
              for(var i=0; i<successors.length; i++){
                
                var edge_lbl = g.outEdges(nodeId, successors[i])[0];
                var edge = edgesAll[edge_lbl];
                var path = edge.select("path");

                var transformText = thisNode.attr("transform");
                var translate = d3.transform(transformText).translate;  //returns [0,-25]

                var x1 = Number(rect.attr("x")) + translate[0];
                var y1 = Number(rect.attr("y")) + translate[1];
                var ht1 = Number(rect.attr("height"));
                var width1 = Number(rect.attr("width"));

                var rect2, x2, y2, ht2, width2, translate2;

                rect2 = nodesAll[successors[i]].select("rect");
                tempNodeId = successors[i];
                var transformText = nodesAll[successors[i]].attr("transform");
                translate2 = d3.transform(transformText).translate;
                
                x2 = Number(rect2.attr("x")) + translate2[0];
                y2 = Number(rect2.attr("y")) + translate2[1];
                ht2 = Number(rect2.attr("height"));
                width2 = Number(rect2.attr("width"));  

                var x1new, x2new, y1new, y2new;
                
                if((y1+ht1) < y2) {
                  y1new = y1+ht1;
                  x1new = x1 + width1/2.0;
                  x2new = x2 + width2/2.0;
                  y2new = y2;
                } else if (y1 > (y2+ht2)){
                  y1new = y1;
                  x1new = x1 + width1/2.0;
                  y2new = y2 + ht2;
                  x2new = x2 + width2/2.0;
                } else if (x1 > (x2+width2)) {
                  y1new = y1 + ht1/2.0;
                  x1new = x1;
                  y2new = y2+ht2/2.0;
                  x2new = x2 + width2;
                } else {

                  y1new = y1+ht1/2.0;
                  x1new = x1 + width1;
                  y2new = y2+ht2/2.0;
                  x2new = x2;  
                }        

                var cx = (x1new+x2new)/2.0;
                var cy = (y1new+y2new)/2.0;

                //Adjust it to take the translation of the edge into account
                var edgeTranslate = d3.transform(edge.attr("transform")).translate;
                x1new = x1new - edgeTranslate[0];
                y1new = y1new - edgeTranslate[1];
                x2new = x2new - edgeTranslate[0];
                y2new = y2new - edgeTranslate[1];   

                // handle the self edges
                if (nodeId===tempNodeId){
                  // console.log("Self loop point reached");
                  path.attr("d", createSelfLoop(x1new, y1new));
                } else {
                  path.attr("d", "M " + x1new + " , " + y1new + 
                    " L " + x2new + " , " + y2new );
                }

                //Update the edgeLabel if there is one
                if(edgeLabelsAll[edge_lbl] == undefined || edgeLabelsAll[edge_lbl] == null){
                  //No edge label
                } else {
                  var edgeLabel = edgeLabelsAll[edge_lbl];
                  // Translate it to the midpoint
                  edgeLabel.attr("transform", "translate(" + cx +  ", " + cy + ")");
                }

              }
            }
  }

  // The main function that sets up the graph, initializes all variables and 
  // sets up event listeners
  function showGraph(graph_id, node_colors) {
    // mcritweb: issue #69 - showGraph runs once per panel, and the handlers it
    // binds at the bottom used to say nothing about which panel they were for.
    // Everything they need comes off the panel table instead:
    //  - panel        the per-panel stores (blocks, colours, loops, both graphs)
    //  - container_id the svg this panel's graph is drawn in
    //  - pane_id      the half of the page holding it; both halves are
    //                 position:relative, so it is also the frame the tooltip is
    //                 positioned and clamped against
    //  - tooltip_id   this panel's tooltip. The single-graph template has one
    //                 #tooltip/#value; this one has a pair per panel.
    var panel = cfgPanels[graph_id];
    var container_id = panel.container;
    var pane_id = panel.pane;
    var tooltip_id = "#tooltip_" + graph_id;
    var value_id = "#value_" + graph_id;

    var svg = d3.select(container_id);
    var inner = d3.select(container_id + " g");

    // Render the graphlib object using d3.
    var renderer = new dagreD3.Renderer();
    // renderer.run(g, d3.select("#graphContainer g"));

    //Render the modified file (output from loopified code) i.e. the file with invisible edges, ports etc.
    // mcritweb: issue #69 - panel.rendered is the graph laid out here, so it is the
    // one the edge ids in the DOM belong to. panel.graph is a second parse of the
    // same string and yields the same ids, so either would resolve; this is simply
    // the object the markup came from, and it does not rely on that holding.
    renderer.run(panel.rendered, d3.select(container_id + " g"));
    // Collapse all nodes by default
    // d3.selectAll("#graphContainer g.node.enter")
    // .each(function(d) { 
        
    //     var thisNode = d3.select(this);
        
    //     var tspans = thisNode.selectAll("tspan");
    //     var rect = thisNode.select("rect");
        
    //     tspans.style("display", "none");
    //     thisNode.select("tspan")
    //         .style("display", "unset");
        
    //     rect.attr("width", "100");
    //     rect.attr("height", "40");
    //     updateEdges(thisNode);    
    // });  


    // Optional - resize the SVG element based on the contents.
    var bbox = svg.node().getBBox();  // getBBox gives the bounding box of the enclosed 
                                      // elements. Its width and height can be set to a different value.

    // svg.node().style.width = "100%";
    // svg.node().style.height = "100%"; 
    if (graph_id == "a") {
      fillNodesandEdgesA(node_colors);
    } else {
      fillNodesandEdgesB(node_colors);
    }
	  

    // MCRIT resize to minimum of width and height instead of fitting only by width
    // var initialScale = parseInt(svg.style("width"), 10) / graph_svg_width;
    var graph_svg_width = bbox.width;
    var graph_svg_height = bbox.height;
    var bounds = d3.select(container_id).node().getBoundingClientRect();
    var initialScale_w = (bounds.width - 16) / graph_svg_width;
    var initialScale_h = (bounds.height - 16) / graph_svg_height;
    var initialScale = Math.min(initialScale_w, initialScale_h);
	 // Set up zoom support
    zoom = d3.behavior.zoom().on("zoom", function() {
      inner.attr("transform", "translate(" + d3.event.translate + ")" +
                                  "scale(" + d3.event.scale + ")");
    });
    
    svg.call(zoom).on("dblclick.zoom", null);
       

	zoom
      // .translate([0 , 20])
      .scale(initialScale)
      .event(svg);

    // mcritweb, issue #74: hand the rendered graph to function_compare.js
    graph_zooms[graph_id] = {zoom: zoom, svg: svg, inner: inner, initialScale: initialScale};
    if (typeof onGraphShown === "function") {
      onGraphShown(graph_id);
    }
  
  var nodes = svg.selectAll("g.node.enter");
  var brush = svg.append("g")
      .attr("class", "brush");

  d3.select("#xcfg_left").on("mouseover", function(){
    isHoverOnLeftPanel = true;
  })
  .on("mouseout", function(){
    isHoverOnLeftPanel = false;
  })

  d3.select("#enableTooltip").on("change", function(){
    isTooltipEnabled = this.checked;
  });

  d3.select("#enableNodeDrag").on("change", function(){
    is_node_dragging_enabled = this.checked;
  });  

  d3.select("#countEncoding").on("change", function(){
    isTripCountShown = this.checked;

    if(isTripCountShown){
      //enable 
          // mcritweb: issue #69 - scoped to this panel and read off its own
          // graph. #countEncoding is not in this template, so none of this runs
          // today; it would have thrown on the null global `g` if it ever did.
          d3.selectAll(container_id + " " + "g.node.enter")
      .each(function(d) { 
        panel.nodes[d] = d3.select(this);
        
          if("degree" in panel.rendered.node(d)){
            var deg = panel.rendered.node(d).degree;
            
            // Change the fill to stroke property
            // nodesAll[d].select("rect").style("fill", degreeScale(Math.log10(deg)));

            panel.nodes[d].select("rect").style("stroke-width", degreeBorderScale(Math.log10(deg)));

          }

       });

    } else {
      //disable 
          d3.selectAll(container_id + " " + "g.node.enter")  // mcritweb: issue #69
      .each(function(d) { 
        panel.nodes[d] = d3.select(this);
        
          if("degree" in panel.rendered.node(d)){
            // var deg = g.node(d).degree;

            // Change this property from fill to stroke-width
            // nodesAll[d].select("rect").style("fill", "");

            panel.nodes[d].select("rect").style("stroke-width", "");

          }

       });

    }

  });

  d3.select("#loopBgFill").on("change", function(){
     // mcritweb: issue #69 - the boundaries are drawn per panel here, so this
     // toggles both groups instead of the one-graph page's single #bgFill.
     isLoopBoundaryShown = this.checked;
     setLoopBoundaryVisibility();
  });

  //enable or disable brush using checkbox
  d3.select("#enableBrush").on("change", function() {
    
    isBrushEnabled = this.checked;

    if(isBrushEnabled && !brushInitialized ) {

      svg.call(zoom).on("dblclick.zoom", null)
        .on("mousedown.zoom", null)
        .on("touchstart.zoom", null)
        .on("touchmove.zoom", null)
        .on("touchend.zoom", null);

      brush.call(d3.svg.brush()
        .x(d3.scale.identity().domain([0, parseInt(svg.node().style.width, 10)]))
        .y(d3.scale.identity().domain([0, parseInt(svg.node().style.height, 10)]))
        .on("brush", function() {
        })
        .on("brushend", function() {

          var extent = d3.event.target.extent();
          currentTempGrp = [];
            
          nodes.classed("selected", function(d) {
            var rect = d3.select(this).select("rect");

            // use axis-aligned rectangle collision code
            // return extent[0][0] <= d.x && d.x < extent[1][0]
           //      && extent[0][1] <= d.y && d.y < extent[1][1];

            var transformText = d3.select(this).attr("transform");
            var translate = d3.transform(transformText).translate;  //returns [tx,ty]

           var x = Number(rect.attr("x")) + translate[0];
           var y = Number(rect.attr("y")) + translate[1];
           var height = Number(rect.attr("height"));
           var width = Number(rect.attr("width"));

           transformText = inner.attr("transform");
           var scale = d3.transform(transformText).scale;
           var translate2 = d3.transform(transformText).translate;

           // console.log(typeof scale[0]);

           x = x*scale[0] + translate2[0];
           y = y*scale[0] + translate2[1]; 
           width = width*scale[0];
           height = height*scale[0]; 

           if(x < extent[1][0]  && x + width > extent[0][0] 
              && y < extent[1][1] && y + height > extent[0][1]) {

              // console.log(d);
              currentTempGrp.push(d3.select(this).datum()); 
              return true;
            }
            
          return false;

          });
        }));
    } else if(isBrushEnabled){

      svg.call(zoom).on("dblclick.zoom", null)
        .on("mousedown.zoom", null)
        .on("touchstart.zoom", null)
        .on("touchmove.zoom", null)
        .on("touchend.zoom", null);

      brush.classed("invisible", false);

    } else {
      svg.call(zoom).on("dblclick.zoom", null);
      brush.classed("invisible", true);
    }
  }); 

	d3.select("#save")
    .on("click", function(){
        var bbox = svg.node().getBBox();
        var text = '<!--?xml version="1.0" encoding="UTF-8" standalone="no"?-->\n';
        // text += '<?xml-stylesheet href="style.css" type="text/css"?>\n';
        text += '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n';
        text += '<svg id="graphContainer"' + ' width="' + parseInt(bbox.width) + 'px" height="' + parseInt(bbox.height) + 'px" viewBox="0 0 '
          + bbox.width + ' ' + bbox.height + '"'
          + ' xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">\n' ;
        
        //Embed the CSS here
        text+= '<defs>\n<style type="text/css"><![CDATA[';

        //get the CSS 
        d3.text("../static/trace_CFG/style.css", function(error, strCSS) {
          if (error) throw error;
          text+= strCSS;
          text+= ']]></style>\n</defs>';

          text += svg.node().innerHTML;
          text += "</svg>";
          saveTextAsFile(text); 
        });

        

    });    
    
    d3.select("#showCycles")
        .on("click", function(){
          setHighlightMode(isCycleShown ? null : "cycles");
        }
      );

    // mcritweb: issue #69 - Show Loops was only ever bound by loopCollapser.init(),
    // which this page does not call, so the button did nothing at all.
    d3.select("#showLoops")
        .on("click", function(){
          setHighlightMode(isLoopShown ? null : "loops");
        }
      );

    /* Disabled this feature, Will collapse instead */
      // $("g.node.enter").click(function(){
      //           $(this).find("tspan").toggle(); 
      //       });  

    // If trace is supplied, find the trace text instead of the CFG text
    // Setup highlighting and linking code to link to trace blocks instead of CFG blocks if trace supplied
    // getCodefromGraph(); 

    d3.select("body")
    	.on("keydown", function(){
    		if( d3.event.keyCode == 8)	{
    			// console.log("Delete key Pressed");

          if(isBrushEnabled)  {
            return;
          }

    			if(currentNode)	{
            // mcritweb: issue #69 - this hid the block first and only then read the
            // global `g` for its edges. `g` is null on this page, so the block went,
            // its edges stayed behind pointing at nothing, there was no way to bring
            // it back, and *then* it threw: 27 visible blocks became 26 with all 36
            // edges still drawn. The lookup now happens first, against the panel the
            // hovered block is actually in, and the edge selection is scoped to that
            // panel - unscoped it would have hidden the other graph's edges too.
            var hovered = cfgPanels[currentNodePanel];
            if(!hovered || !hovered.rendered){
              return;
            }
            var nodeId = d3.select(currentNode).datum();
            var inEdges = hovered.rendered.inEdges(nodeId);
            var outEdges = hovered.rendered.outEdges(nodeId);
    				d3.select(currentNode)
    					.style("display", "none");
            if(inEdges.length != 0 || outEdges.length != 0) {
              d3.selectAll(hovered.container + " " + "g.edgePath.enter")
                .filter(function(d){
                  for(var i=0; i<inEdges.length; i++){
                    if(inEdges[i] === d){
                      return true;
                    }
                  }
                  for(var i=0; i<outEdges.length; i++){
                    if(outEdges[i] === d){
                      return true;
                    }
                  }
                  return false;    
                })
                .style("display", "none");
                
            }
    			}
    		} 

    		else if(d3.event.keyCode == 71) { //Pressed key 'g'
          		
          		if(currentTempGrp.length > 1){

                // console.log(currentTempGrp);
                
            		nodeGroups.push(currentTempGrp);
            		var grpName = prompt("Give a name for the group", nodeGroups.length);
            		nodeGroupMeta.push(grpName);
            		var index = nodeGroups.length - 1;
            		isNodeGrpCollapsed.push(false);
                nodeGroupsArray[index] = {};

            		var div_nodegrps = d3.select("#nodeGrpView");
            		div_nodegrps.append("p")
            			.text(grpName)
            			.datum(index)
            			.on("dblclick", function(){
            				
		                    var index = d3.select(this).datum();

		                    if(isNodeGrpCollapsed[index] === true)	{
		            					// uncollapse


                    for(var i = 0; i < nodeGroups[index].length; i++){
                                
                                var nodeId = nodeGroups[index][i];
                                // console.log(nodeId);
                                var inEdges = g.inEdges(nodeId);
                                var outEdges = g.outEdges(nodeId);

                                for(var j=0; j<inEdges.length; j++) {
                                  edgesAll[inEdges[j]].style("display", "unset");
                                }
                                for(var j=0; j<outEdges.length; j++) {
                                  edgesAll[outEdges[j]].style("display", "unset");
                                }

                                nodesAll[nodeId].style("display", "unset");
                                

                          }

                                // unhideAllNodesandEdges(index);
                                
                                hideNodeAndEdgesofGrp(index);
    


		            					// alert("Will unCollapse");
		            					isNodeGrpCollapsed[index] = false;
		            		}	else {
		            					
                          

                          

		                      	// collapse
			    					for(var i = 0; i < nodeGroups[index].length; i++){
				                        
				                        var nodeId = nodeGroups[index][i];
				                        // console.log(nodeId);
				                        var inEdges = g.inEdges(nodeId);
				                        var outEdges = g.outEdges(nodeId);

				                        for(var j=0; j<inEdges.length; j++) {
				                          edgesAll[inEdges[j]].style("display", "none");
				                        }
				                        for(var j=0; j<outEdges.length; j++) {
				                          edgesAll[outEdges[j]].style("display", "none");
				                        }

                                nodesAll[nodeId].style("display", "none");
				                        

		                      }

                                // hideAllNodesandEdges(index);
                                
                              if(!("isRendered" in nodeGroupsArray[index]) ||  nodeGroupsArray[index]["isRendered"] === false)  {
                                  grpbbox = computeBoundingBox(index);
                                  insertNodeAndEdgesofGrp(index, grpbbox);
                                  nodeGroupsArray[index]["isRendered"] = true;
                              } else {

                                nodesAll[index].style("display", "unset");
                                for (var i = 0; i < nodeGroupsArray[index]["edges"].length; i++){
                                  edgesAll[nodeGroupsArray[index]["edges"][i]].style("display", "unset");
                                }

                              }
		                      // alert("Will collapse");
		            					isNodeGrpCollapsed[index] = true;
		            		}
            		});


          		}
        	} 	
    	});

    var drag = d3.behavior.drag()  
     .on('dragstart', function() { 

      if(!is_node_dragging_enabled){
          return;
      }

     //Do Something 
      d3.event.sourceEvent.stopPropagation();
        // console.log("Event not propagated");
        
     })
     .on('drag', function() { 

        if(!is_node_dragging_enabled){
          return;
        }

        d3.select(this).attr("transform", "translate(" + d3.event.x + "," + d3.event.y + ")");
        updateEdges(d3.select(this));
                             })
     .on('dragend', function() { 

        if(!is_node_dragging_enabled){
          return;
        }
        updateEdges(d3.select(this)); 
     });


     var rtClickdrag = d3.behavior.drag()  
     .on('dragstart', function() { 

        // If right click was detected
        if(d3.event.sourceEvent.button == 2){
          

          console.log("Right click detected");
          // debugger;
          d3.event.sourceEvent.preventDefault();

          rtDragStart[0] = d3.event.sourceEvent.x;
          rtDragStart[1] = d3.event.sourceEvent.y;
          var rtDragTransform = d3.transform(d3.select(this).select("g").attr("transform"));  
          rtDragTranslate = rtDragTransform.translate;
          rtDragScale = rtDragTransform.scale;
          isRtDragStarted = true;

          

      }       
     })
     .on('drag', function() { 
        if(isRtDragStarted){
          d3.select(this).select("g").attr("transform", "translate(" + ((d3.event.x - rtDragStart[0])*3 + rtDragTranslate[0]) + "," 
            + ((d3.event.y - rtDragStart[1])*3 + rtDragTranslate[1]) + ") scale (" + rtDragScale[0] + ")");  
        }
        
     })
     .on('dragend', function() { 
         isRtDragStarted = false;
     });

     // d3.select("#graphContainer").on("contextmenu", function(){})
     // .call(rtClickdrag);


    // mcritweb: issue #69 - scoped to this panel. Unscoped, the second panel to
    // finish rendering rebound the first panel's edges to its own graph as well,
    // so an edge of A was looked up in B's graph.
    d3.selectAll(container_id + " " + "g.edgePath.enter")
      .on("mouseover", function(){


        // d3.select(this).select("path").attr("stroke-width", "2.5");
        // d3.select(this).select("path").attr("stroke", "teal");  
        
        d3.select(this).classed("active", true);

      })
      .on("click", function(){
        
        // mcritweb: issue #69 - was `g._strictGetEdge(...)` and `nodesAll[...]`.
        // The global `g` is assigned by the single-graph page's loader and stays
        // null here, so every edge click threw "Cannot read properties of null
        // (reading '_strictGetEdge')" on this line and moved nothing. Both the
        // graph and the rendered blocks now come from this panel.
        var incidences = panel.rendered._strictGetEdge(d3.select(this).datum());
        var u = incidences.u;
        var v = incidences.v;
        
        var clickPt = d3.mouse(this);     

        var uTransform = panel.nodes[u].attr("transform");
        var vTransform = panel.nodes[v].attr("transform");

        var ut = []
        var vt = [];
        
        if(d3.transform(uTransform).translate[0] > d3.transform(vTransform).translate[0]){
          ut[0] = clickPt[0] + 30;
          vt[0] = clickPt[0] - 30;  
        } else {
          ut[0] = clickPt[0] - 30;
          vt[0] = clickPt[0] + 30;
        }

        if(d3.transform(uTransform).translate[1] > d3.transform(vTransform).translate[1]){
          ut[1] = clickPt[1] + 30;
          vt[1] = clickPt[1] - 30;  
        } else {
          ut[1] = clickPt[1] - 30;
          vt[1] = clickPt[1] + 30;
        }        

        panel.nodes[u].transition().duration(500)
        .ease("exp-out")
        .attr("transform", "translate(" + ut[0] + "," + ut[1] + ")");
        
        panel.nodes[u].transition().duration(250).delay(1000)
        .ease("exp-in")
        .attr("transform", uTransform);

        panel.nodes[v].transition().duration(500)
        .ease("exp-out")
        .attr("transform", "translate(" + vt[0] + "," + vt[1] + ")");
        
        panel.nodes[v].transition().duration(250).delay(1000)
        .ease("exp-in")
        .attr("transform", vTransform);

      })
      .on("mouseout", function(){
        // d3.select(this).select("path").attr("stroke-width", "1");
        // d3.select(this).select("path").attr("stroke","black");  

        d3.select(this).classed("active", false);

    });

      
    // mcritweb: issue #69 - scoped to this panel, so the handlers below know which
    // of the two tooltips and which block registry they are for.
    d3.selectAll(container_id + " " + "g.node.enter")
      // .on("click", function()){
      // 		currentNode = this;
      // })	

      /*
      .on("dblclick", function(){

        // d3.event.sourceEvent.stopPropagation();   
    		
        var tspans = d3.select(this).selectAll("tspan");
    		var rect = d3.select(this).select("rect");
        var thisNode = d3.select(this);
    		var height = rect.node().getBBox().height;
    		// console.log(height);

    		num_lines = tspans.size();
    		// console.log(num_lines);	

    		tspans.style("display", "none");
    		d3.select(this).select("tspan")
            .style("display", "unset");
    		
    		rect.attr("height", "40");

        updateEdges(thisNode);

    		

      })
      */

      .on("mouseover", function(){

        if(isBrushEnabled)  {
          return;
        }
        var thisNode = d3.select(this);
        mouseOverGFill = thisNode.attr("fill");
        var rect = thisNode.select("rect");
        mouseOverColor = rect.style("fill");

        // thisNode.attr("fill", "white");
        // rect.style("fill", "teal");
        
        thisNode.classed("active", true);

        currentNode = this;
        currentNodePanel = graph_id;  // mcritweb: issue #69
        var nodeId = thisNode.datum(); 

        var textToHighlight = [];

        // Linked Highlighting
        // mcritweb: issue #69 - this used to branch on isTraceSupplied, and the
        // "no trace" arm fell back to "#text_code p", the paragraphs of the code
        // panel the single-function page renders beside its graph. This template
        // has no #text_code at all, and d3 3.4.11 answers a miss with a selection -
        // an array holding one empty group - rather than with nothing, so the
        // length test below passed, `[0]` was a plain Array, and `.node()` threw
        // "textToHighlight[i].node is not a function" on every hover of every
        // block. With no code panel to link to, both arms became the same lookup,
        // so there is no branch left to make.
        if(nodeId in nodeToTextGroups){
          textToHighlight = nodeToTextGroups[nodeId];
        }
        
        for(var i=0; i<currTextHighlight.length; i++){
          // d3.select(currTextHighlight[i]).style("border-style", "none");
          d3.select(currTextHighlight[i]).classed("highlight", false); 
        }

        currTextHighlight = [];

        for(var i=0; i < textToHighlight.length; i++){
          currTextHighlight.push(textToHighlight[i].node());
          // textToHighlight[i].style("border-color", "teal")
          //   .style("border-style", "solid")
          //   .style("border-width", "2px");

          textToHighlight[i].classed("highlight", true);
          // mcritweb: issue #69 - "scroll the code panel to the first matching
          // block" used to be here, hardcoded to #xcfg_right. On this page that id
          // is the *other graph*, so the one thing it could not do is scroll a code
          // panel; it survived only because textToHighlight is always empty here.
          // Whoever gives this page a code panel scrolls that panel, by its own id.

        }
         
         if(isTooltipEnabled){

            // mcritweb: issue #69 - "Enable Tooltip" was a dead control: it set
            // the flag, and everything below then wrote to "#tooltip"/"#value",
            // which the single-graph template has and this one does not - it has
            // #tooltip_a/#value_a and #tooltip_b/#value_b. The panel this handler
            // was bound for now supplies all four of the ids, and the position is
            // taken against that panel's own div rather than always the left one.

            //Get the mouse event's x/y values relative to the containing div
            var pos = [0,0];
            var frame = d3.select(pane_id).node();
            pos = d3.mouse(frame);
            var xPosition = pos[0];	
            var yPosition = pos[1];

            // mcritweb: issue #69 - and this is why the two are one change. The
            // lines below are the dot graph's node label, which carries the api
            // names smda read out of the analysed binary (`toDotGraph(with_api=
            // True)`) - so whoever gets a sample submitted writes part of them.
            // They used to be joined with "<br/>" and assigned into .innerHTML,
            // which made an import called `<img src=x onerror=...>` run. Nothing
            // reached the sink only because the hover threw a few lines above, so
            // repairing either of those two arms it. Joined with newlines and set
            // as text instead; the tooltip is told to keep the line breaks.
            var text = "";
            thisNode.selectAll("text tspan").each(function(){
              text+=d3.select(this).text() + "\n";
            });

            // mcritweb: issue #69 - getBBox is in the graph's own units while the
            // graph is drawn at whatever the zoom currently is, so an unscaled width
            // was wrong by that factor, and nothing held the result inside the panel:
            // a wide block gave a 344px tooltip in a 558px half and overflowed it by
            // 43.7px, clipping the text. Scaled, then clamped to the frame, with the
            // left edge clamped so the right edge lands inside it too.
            var graph_transform = inner.attr("transform");
            var graph_scale = graph_transform ? d3.transform(graph_transform).scale[0] : 1;
            var box_padding = 6;  // the 3px this handler sets, on both sides
            var frame_width = frame.clientWidth;
            var width = rect.node().getBBox().width * graph_scale * 1.25;
            width = Math.min(width, frame_width - box_padding - 2);
            xPosition = Math.max(0, Math.min(xPosition, frame_width - width - box_padding - 1));

            //Update the tooltip position and value
            // The stylesheet under static/trace_CFG/ is vendored and styles
            // "#tooltip" and "#tooltip p" by id, so it reaches neither of this
            // page's two; the properties that make the tooltip readable are set
            // here instead, and they are the same ones - without margin:0 the
            // paragraph keeps Bootstrap reboot's 16px margin-bottom inside a box
            // padded by 3.
            d3.select(tooltip_id)
              .style("position", "absolute")
              .style("z-index", "10")
              .style("padding", "3px")
              .style("border-radius", "10px")
              .style("box-shadow", "4px 4px 10px rgba(0, 0, 0, 0.4)")
              .style("background-color", "white")
              .style("pointer-events", "none")
              .style("white-space", "pre")
              .style("font-family", "monospace")
              .style("left", xPosition + "px")
              .style("top", yPosition + "px")
              .style("width", function(){return width + "px"})
              .select(value_id)
              .text(text);

            d3.select(tooltip_id).select("p")
              .style("margin", "0")
              .style("font-size", "1.25em");

            //Show the tooltip
            d3.select(tooltip_id).classed("hidden", false);
          }

      })
      .on("mouseout", function(){

        if(isBrushEnabled)  {
          return;
        }

        // d3.select(this).attr("fill", mouseOverGFill);  
        // d3.select(this).select("rect").style("fill", mouseOverColor);
        d3.select(this).classed("active", false);

        // for(var i = 0; i<currTextHighlight.length; i++){
        //         d3.select(currTextHighlight[i]).style("border-style", "none");
        //         d3.select(currTextHighlight[i]).classed("highlight", false);
        // }

        // currTextHighlight = [];
        currentNode = null;
        currentNodePanel = null;  // mcritweb: issue #69
        
        //Hide the tooltip
        d3.select(tooltip_id).classed("hidden", true);  // mcritweb: issue #69

      })
    .call(drag);

    // setupHighlight();
      
  }

  /*
  function setupHighlight(){
    $('#text_code').mouseup(function(e) {
        var selection = getSelected();
        if(selection && (selection = new String(selection).replace(/^\s+|\s+$/g,''))) {
   
          // alert(selection);

          var posX = $(this).offset().left,
            posY = $(this).offset().top;
          // alert((e.pageX - posX) + ' , ' + (e.pageY - posY));

          countlines(e.pageX - posX, e.pageY - posY);
          
        }
      });
    
  }

  function getSelected() {
    if(window.getSelection) { return window.getSelection(); }
    else if(document.getSelection) { return document.getSelection(); }
    else {
      var selection = document.selection && document.selection.createRange();
      if(selection.text) { return selection.text; }
      return false;
    }
    return false;
  }

  function countlines(rel_posX, rel_posY){
    pre_element = document.getElementById('text_code');
    height = pre_element.offsetHeight;
    console.log(height);
    //lineHeight = parseInt(element.style.lineHeight);

    //lineHeight = document.defaultView.getComputedStyle(document.getElementById('right'), null).getPropertyValue("lineHeight");

    textContent = pre_element.textContent;
    lines = textContent.split(/\r\n|\r|\n/).length

    // console.log(lineHeight);
    // lines = height / lineHeight;
    
    // alert("Lines: " + lines);

    // rel_posX, rel_posY are relative to the div#right element

    // alert(rel_posY + ", " +  height);
    alert( "Line No: " + Math.ceil( rel_posY / height * lines));
    
    var gnode = $("g.node.enter rect")[0];
    console.log(gnode);
    // gnode.setAttribute("style", "color:#FFF; background-color:#DF3D82;");  
    gnode.style.fill = "#DF3D82";  
  }

*/

  // Sets up trace
  // Works on the traceText global variable
  // Sets up markers by matching the first address of the CFG basic block with the address on the line of trace
  // A CFG contains an instruction in only one of the basic blocks.
  // Scan through the trace, add start when encountering the first addr of one of the basic blocks
  // End marker when the ending address arrives
  // For the next line, repeat the process
  function setupTrace(){

    // console.log(g);

    var nodes = g.nodes();
    var num_nodes = nodes.length;

    // loop through all the nodes and setup dicts of firstAddress and lastAddress with the nodeIds
    for(var i=0; i<num_nodes; i++)  {
      var nodeId = nodes[i];
      var label = g.node(nodeId).label;
      // console.log(label);
      var temp = label.split('\\n');
      if(temp.length > 1){
        var firstAddr = temp[1].split(':')[0];
        var lastAddr = temp[temp.length-1].split(':')[0];
        // console.log(firstInstr + " : " + lastInstr);

        // Create an entry in the dict of node addresses
        nodesEndAddress[nodeId] = {startAddr: firstAddr, endAddr: lastAddr};
        // Also create an entry in the dict of start addresses for fast lookup of node with the start Address in the trace
        startAddrNode[firstAddr] = nodeId;
      }
    }
  
    // loop through the trace file and add it to the array of trace instruction block
    // Make p elements for every such block
    
    var documentFragment = document.createDocumentFragment();
    documentFragment = d3.select(documentFragment);

    var traceLines = traceText.split("\n");
    var num_lines = traceLines.length;
    // console.log(num_lines);

    var instrs = [];
    var isBlockStarted = false;
    var currStartAddr = "";
    var currEndAddr = "";
    var currLine = "";
    var currAddr = "";
    var tempSplit = [];
    var currNodeId = "";
    var currCodeBlock = "";
    var currInstr = {};
    var isNonMatch = false;

    var codeBlocks = [];

    for(var i=0; i<num_lines; i++) {
      currLine = traceLines[i];
      if(currLine.split(' ').length < 2){
        currCodeBlock += currLine + "\n";
        continue;
      }
      currAddr = currLine.split(' ')[1];
      // console.log(currAddr);

      if(!isBlockStarted){

        currNodeId = startAddrNode[currAddr];
        
        if(currNodeId == undefined){
          if(isNonMatch){
            currCodeBlock += currLine + "\n";
          } else {
            currCodeBlock = currLine + "\n";
          }
          isNonMatch = true;
          continue;
        } else if(isNonMatch){
          isNonMatch = false;
          //TODO:add the node here
          // This is the text block which has no matching node in CFG
          // Currently, the tool ignores these text
          // Adding them to the right with other text blocks may introduce some errors in the
          // autoscrolling logic
          // Need to make sure nothing breaks if this is added as one of the p blocks
          // Currently, the assumption is that no matter where the user has scrolled, we can find at least one corresponding node in the CFG
          // and apply the gradient
          // Also the index of the p block is closely tied to code block array, text block array, text block offset array etc.
        }


        currInstr = {nodeId: currNodeId};
        
        currStartAddr = nodesEndAddress[currNodeId].startAddr;
        currEndAddr = nodesEndAddress[currNodeId].endAddr;

        // Reset the code block
        currCodeBlock = "";
        isBlockStarted = true;
      }

      currCodeBlock += currLine + "\n";

      if(currAddr === currEndAddr){
        // add the code block to the docFragment
        isBlockStarted = false;
        // currInstr.codeBlock = currCodeBlock;
        codeBlocks.push(currCodeBlock);

        instrs.push(currInstr);
        if (!(currNodeId in nodeToTextGroups)) {
          nodeToTextGroups[currNodeId] = [];
        }

        //documentFragment makes the custom styling not possible. So, although its more efficient, do it the normal way instead using enter selection in d3.
        
        // nodeToTextGroups[currNodeId].push(documentFragment.append("p").datum(currInstr).text(currCodeBlock));

        // var elem = documentFragment.append("p").datum(currInstr).text(currCodeBlock);
        // nodeToTextGroups[currNodeId].push(elem);
        // textBlocksArray.push(elem);
      } 
    }
    

    // console.log(documentFragment.node());
    // d3.select("#graphContainer").style("display", "none");	// Reduces reflow i.e. repeated rendering during the appending of document fragment to the right div
    // d3.select("#text_code").node().appendChild(documentFragment.node());
    // d3.select("#graphContainer").style("display", "unset");

    d3.select("#text_code")
      .selectAll("p")
      .data(instrs)
      .enter()
      .append("p")
      .text(function(d, i){
        return codeBlocks[i];
      });

    d3.selectAll("#text_code p")
      .each(function(d,i){
          var thisNode = d3.select(this);
          textBlocksArray[i] = thisNode;
          nodeToTextGroups[d.nodeId].push(thisNode);
          textBlocksOffset.push({p:thisNode, start:this.offsetTop, end: this.offsetTop + this.clientHeight});
      })
      .on("mouseover", function(d){

        // Get the associated nodeID
        var nodeId = d.nodeId;  
        
        // d3.select(currNodeHighlight).select("rect").style("fill", "white"); 
        d3.select(currNodeHighlight).classed("highlight", false);

        currNodeHighlight = nodesAll[nodeId].node();
        // d3.select(currNodeHighlight).select("rect").style("fill", "teal");
        d3.select(currNodeHighlight).classed("highlight", true);

        d3.select(this).classed("active", true);
        currentText = this;

      })
      .on("mouseout", function(d){

        d3.select(this).classed("active", false);  
        
        //Get the nodeId
        var nodeId = d.nodeId;
        if(currNodeHighlight){
          // d3.select(currNodeHighlight).select("rect").style("fill", "white");
          // d3.select(currNodeHighlight).classed("highlight", false);
        }
        // currNodeHighlight = null;
        currentText = null;

      })
      /*
      .on("click", function(d, i){
        d3.select(currStartTextNode).classed("start", false);
        currStartTextNode = this;
        prevStartTextIndex = currStartTextIndex;
        prevEndTextIndex = currEndTextIndex;

        currStartTextIndex = i;
        d3.select(this).classed("start", true);
        applyGradient(currStartTextIndex, currEndTextIndex, prevStartTextIndex, prevEndTextIndex);

      })
      .on("dblclick", function(d,i){
        d3.select(currEndTextNode).classed("end", false);
        currEndTextNode = this;
        prevStartTextIndex = currStartTextIndex;
        prevEndTextIndex = currEndTextIndex;

        currEndTextIndex = i;
        d3.select(this).classed("end", true);
        applyGradient(currStartTextIndex, currEndTextIndex, prevStartTextIndex, prevEndTextIndex);

      })
      */
      .on("click", function(d){
        if(d.nodeId in nodesAll){
          // scrollToNode(nodesAll[d.nodeId]);
        }
      });

      var divRight = d3.select("#xcfg_right");
      last_known_scroll_position = divRight.node().scrollTop;
      last_known_panel_height = divRight.node().offsetHeight;

      autoHighlightOnScroll(last_known_scroll_position, 
        last_known_panel_height);

      divRight
        .on("scroll", function() {
        last_known_scroll_position = this.scrollTop;
        last_known_panel_height = this.offsetHeight;
        // console.log(this.scrollTop + " scrolled");

        if(!ticking)  {
          window.requestAnimationFrame(function(){
            autoHighlightOnScroll(last_known_scroll_position, last_known_panel_height);
            // console.log("Rendered on scroll " + last_known_scroll_position);
            ticking = false;
          });
        }
        ticking = true;
      });


  }

  function showGradient(){
    var bar = d3.select("#gradient");

    var colorScale = d3.scale.linear()
    .domain([0,150])
    // .range(["#deebf7", "#3182bd"])

    // Tone down the gradient

    .range(["#deebf7", "#70aad3"])
    .interpolate(d3.interpolateHcl);

    var data = d3.range(0,148,5);

    bar.selectAll("div").data(data)
      .enter().append("div").style("width", "5px")
      .style("height", "100%")
      .style("position", "absolute")
      .style("left", function(d){ return d + "px"})
      .style("background-color", function(d){return colorScale(d)});

  }

  // Applies gradient: a = starting index of the textblock, b = ending index of the textblock
  // prev_a & prev_b are used to clear the previous coloring
  function applyGradient(a,b, prev_a, prev_b){
    var color;

    var colorScale = d3.scale.linear()
    .domain([a,b])
    // .range(["#deebf7", "#3182bd"])
    .range(["#deebf7", "#70aad3"])
    .interpolate(d3.interpolateHcl);

    for(var i = prev_a; i<=prev_b; i++){
      if(i >= 0 && i < textBlocksArray.length){
        nodesAll[textBlocksArray[i].datum().nodeId].select("rect").style("fill", "");
        textBlocksArray[i].style("background-color", "");
      }
    }

    for(var i = a; i<=b; i++){
      if(i >= 0 && i < textBlocksArray.length){
        color = colorScale(i);
        nodesAll[textBlocksArray[i].datum().nodeId].select("rect").style("fill", color);
        textBlocksArray[i].style("background-color", color);
      }
    }

    var u,v; 
    var edge;
    for (var i=prev_a+1; i<=prev_b; i++){
      if(i>0 && i<textBlocksArray.length){
        u = textBlocksArray[i-1].datum().nodeId;
        v = textBlocksArray[i].datum().nodeId;

        edge = g.outEdges(u,v)[0];
        if(edge === undefined){
          continue;
        }
        edgesAll[edge].classed("highlight", false);
        edgeLabelsAll[edge].classed("highlight", false);
      }
    }

    for (var i=a+1; i<=b; i++){
      if(i>0 && i<textBlocksArray.length){
        u = textBlocksArray[i-1].datum().nodeId;
        v = textBlocksArray[i].datum().nodeId;

        edge = g.outEdges(u,v)[0];
        if(edge === undefined){
          continue;
        }
        edgesAll[edge].classed("highlight", true);
        edgeLabelsAll[edge].classed("highlight", true);
      }
    }

    // Disable animation for autoscroll highlighting
    // animateTracePath(a,b, prev_a, prev_b);

  }

  function animateTracePath(a,b, prev_a, prev_b){
    
    if(a<0 || b < 0 || a >= textBlocksArray.length || b >= textBlocksArray.length){
      return;
    }

    var colorScale = d3.scale.linear()
    .domain([a,b])
    // .range(["#deebf7", "#3182bd"])
    .range(["#deebf7", "#70aad3"])
    .interpolate(d3.interpolateHcl);

    //cancel previous transitions
    for(var i=prev_a; i<=prev_b; i++){
      if(i>=0 && i<textBlocksArray.length){
        textBlocksArray[i].transition().duration(0).style("background-color", "");
        var rect = nodesAll[textBlocksArray[i].datum().nodeId].select("rect");
        rect.transition().duration(0).style("fill", "");
      } 
    }

    textBlocksArray.slice(a,b+1).forEach(function(block, index){
        setTimeout(function(){
            // var blockColor = block.style("background-color");
            var rect = nodesAll[block.datum().nodeId].select("rect");
            var nodeColor = colorScale(a+index);
            // block.transition().duration(250).style("background-color", "#f1b3a7").transition().style("background-color", "");
            block.transition().duration(250).style("background-color", "#f1b3a7").transition().style("background-color", nodeColor);
            rect.transition().duration(250).style("fill", "#f1b3a7").transition().style("fill", nodeColor);
        },
        500 * index);
    })
    
    // Sample staggered animation in d3
    // d3.selectAll("#text_code p")
    //   .filter(function(d,i){
    //     return i >=a && i <=b;
    //   })
    //   .style("background-color", "blue")
    //   .transition()
    //   .duration(1000)
    //   .delay(function(d,i){return i*2000;})
    //   .style("background-color", "orange");

  }

  function interpolateSearch(arr, value, key){

     var lo = 0;
     var hi = arr.length - 1;
     var mid = -1;
     // var comparisons = 1;      
     var index = -1;

     while(1) {

        if(lo>=hi || arr[lo].key == arr[hi].key){
          
          break;
        }
        // console.log("Comparison:" + comparisons);
        // console.log(lo + " : " + arr[lo].key );
        // console.log(hi + " : " + arr[hi].key );
        // comparisons++;

        // probe the mid point 
        mid = lo + Math.floor(((hi - lo) / (arr[hi].key - arr[lo].key)) * (value - arr[lo].key));
        // console.log("mid = " + mid);

        // value found 
        if(arr[mid].key == value) {
           index = mid;
           break;
        } else {
           if(arr[mid].key < value) {
              // if value is larger, value is in upper half 
              lo = mid + 1;
           } else {
              // if value is smaller, value is in lower half 
              hi = mid - 1;
           }
        }               
     }
     
     // console.log("Total comparisons: " + --comparisons);
     return index;

  }


  function autoHighlightOnScroll(scrollTop, panelSize){

        //Search the textblocks that are in the current view
        //Those textblocks that are within the range: [scrollTop, scrollTop+offsetHeight] 
        // i.e. bottom end greater than top end of the view
        // and top end less than bottom end of the view

        //TODO:Use interpolation search instead, use currentStartIndex as midpoint
        var foundStart = false;
        
        prevStartTextIndex = currStartTextIndex;
        prevEndTextIndex = currEndTextIndex;

        var num_blocks = textBlocksOffset.length;
        var i=0;
        for(; i<num_blocks; i++){
          if(textBlocksOffset[i].end >= scrollTop )  {
            if(textBlocksOffset[i].start <= scrollTop+panelSize){
              if(!foundStart){
                currStartTextIndex = i;
                currStartTextNode = textBlocksOffset[i].p;
                foundStart = true;
              }
              
            } else {
              break;
            }
          }
        }
        
        //TODO: This can be zero; Make it 1 to avoid out of bounds array indexing. Check if it causes other problems.
        // Making it non zero does not fix the problem.
        // if(i==0){
        //   i = 1;
        // }
        
        currEndTextIndex = i-1;
        currEndTextNode = textBlocksOffset[i-1].p;
        
        applyGradient(currStartTextIndex, currEndTextIndex, prevStartTextIndex, prevEndTextIndex);

        scrollToNode(nodesAll[textBlocksArray[currStartTextIndex].datum().nodeId]);

        // Update currStartTextIndex, currEndTextIndex
        // prevStartTextIndex = currStartTextIndex;
        // prevEndTextIndex = currEndTextIndex;
        // currStartTextIndex = i;
        // currEndTextIndex = i;
        // currStartTextNode = this;
        // currEndTextNode = this;
        // applyGradient(currStartTextIndex, currEndTextIndex, prevStartTextIndex, prevEndTextIndex);

  }

  // If trace text not supplied, switches to CFG-only mode
  // Extracts text from CFG, sorts it and places it in the right panel
  // Wires up linked highlighting
  // TODO:Add linked scrolling to trace and non-trace version
  function getCodefromGraph(){
    var nodes = g.nodes();
    var num_nodes = nodes.length;
    for (var i=0; i<num_nodes; i++)  {
      var nodeId = nodes[i];
      // MCRIT: here we can process any custom information we provided into the graph
      var this_node = g.node(nodeId)
      var label = this_node.label;
      label = label.trim();
      label = label.replace(/^{|}$/g, '');
      label = label.replace(/\\n/g, "\n");
      
      // Handle both the static and dynamic trace
      // Static trace has a format like "{%1:\n....}""
      // Dynamic trace has a format like "\n401233f:push dl....\n"
      // Dyn trace does not contain '%', may contain ':'
      
      // var firstInstr = label.substring(0, label.indexOf("\n"));
      var firstInstr = label.split('\n', 1)[0];
      
      // firstInstr = firstInstr.substring(firstInstr.indexOf("%") + 1, firstInstr.indexOf(":"));
      firstInstr = firstInstr.split(':', 1)[0];
      firstInstr = firstInstr.substring(firstInstr.indexOf("%") + 1);
      
      var code = {};
      code['block_result'] = {};
      code['nodeId'] = nodeId;
      code['picblockhash'] = picblockhash;
      code['firstInstr'] = firstInstr;
      code['label'] = label;
      code['block_display'] = label;
      code['block_color'] = "white";
      
      var picblockhash = this_node.comment;
      if (picblockhash) {
        var hash_only = picblockhash.substring(picblockhash.indexOf('x') + 1);
        
        var xmlHttp = new XMLHttpRequest();
        xmlHttp.open( "GET", "../getPicBlockMatches/" + hash_only, false ); // false for synchronous request
        xmlHttp.send( null );
        var result_json = JSON.parse(xmlHttp.responseText);
        code['block_result'] = result_json["data"]
        code['block_display'] = ">>> Matches: " + result_json["families"] + " families, " + result_json["samples"] + " samples, " + result_json["functions"] + " functions.\n" + label;
        // code['block_color'] = "red";
      }
      codes[i] = code;
    }    
    
    // sort the codeblocks based on the addresses or labels
    codes.sort(function(a,b){
      // MCRIT fix: sort after parsing by int instead of subtracting strings
      return parseInt(a.firstInstr,16) - parseInt(b.firstInstr, 16); 
    });

    // MCRIT TODO this actually draws the text, so here we might want to do custom fancy stuff and addtions
    d3.select("#text_code")
      .selectAll("p")
      .data(codes)
      .enter()
      .append("p")
      .style("background", function(d){
        return d.block_color;
      })
      .text(function(d){
        return d.block_display;
      });

    d3.select("#text_code")
      .selectAll("p")
      .each(function(d){
        nodeToTextGroups[d.nodeId] = [d3.select(this)];
      })
      .on("mouseover", function(d){

        // Get the associated nodeID
        var nodeId = d.nodeId;  
        
        // d3.select(currNodeHighlight).select("rect").style("fill", "white"); 
        d3.select(currNodeHighlight).classed("highlight", false);

        if (nodeId in nodesAll) {
          currNodeHighlight = nodesAll[nodeId].node();
          
        } else {
          currNodeHighlight = null;
        }
        // d3.select(currNodeHighlight).select("rect").style("fill", "teal");
        d3.select(currNodeHighlight).classed("highlight", true);

        d3.select(this).classed("active", true);
        currentText = this;

      })
      .on("mouseout", function(d){

        d3.select(this).classed("active", false);  
        
        //Get the nodeId
        var nodeId = d.nodeId;
        if(currNodeHighlight){
          // d3.select(currNodeHighlight).select("rect").style("fill", "white");
          // d3.select(currNodeHighlight).classed("highlight", false);
        }
        // currNodeHighlight = null;
        currentText = null;

      })
      .on("click", function(d){
        if(d.nodeId in nodesAll){
          scrollToNode(nodesAll[d.nodeId]);
        }
      }); 

  }

  // Shows cycle nodes in different colors
  // mcritweb: issue #69 - this used to run against the global g, which this page
  // never assigns, so every click threw. Each panel is coloured from its own graph.
  function showCycles(){
    for(var key in cfgPanels) {
      var panel = cfgPanels[key];
      colorNodeGroups(panel, panel.graph ? graphlib.alg.findCycles(panel.graph) : []);
    }
  }

  // Shows the nodes of each natural loop in different colors.
  // mcritweb: issue #69 - the single-graph page gets this from loopCollapser.init(),
  // which the duo loaders cannot call: it is a singleton over the same globals and
  // binds one button, so the second panel would simply displace the first. The
  // loops themselves come from the server, per panel, and colouring them is the
  // whole of what the button does.
  function showLoops(){
    for(var key in cfgPanels) {
      var panel = cfgPanels[key];
      var groups = [];
      for(var i=0; i<panel.loops.length; i++) {
        var loop = panel.loops[i];
        groups.push(loop && loop.nodes ? loop.nodes : []);
      }
      colorNodeGroups(panel, groups);
    }
  }

  // Paints one color per group over the nodes of a single panel.
  function colorNodeGroups(panel, groups){
    for(var i=0; i<groups.length; i++) {
      var group = groups[i] || [];
      for(var j=0; j<group.length; j++) {
        // a loop or cycle may name a node the renderer dropped, and the names come
        // off the wire - hasOwnProperty so that "constructor" and friends cannot
        // reach Object.prototype and break the whole highlight
        if(!panel.nodes.hasOwnProperty(group[j])) { continue; }
        var node = panel.nodes[group[j]];
        node.attr("fill", "white")
          .select("rect")
          .style("fill", colores_g[i%colores_g.length]);
      }
    }
  }

  // Puts every node back to the per-block diff color it was rendered with, so
  // switching a highlight off does not strip the comparison this page is for.
  function resetNodeFills(){
    for(var key in cfgPanels) {
      var panel = cfgPanels[key];
      for(var nodeId in panel.nodes) {
        if(!panel.nodes.hasOwnProperty(nodeId)) { continue; }
        panel.nodes[nodeId]
          // null removes the attribute; "" would leave fill="" behind, which is not a
          // valid SVG presentation value. Browsers ignore it either way, but a pristine
          // node has no fill attribute at all and un-highlighting should restore that.
          .attr("fill", null)
          .select("rect")
          .style("fill", panel.colors.hasOwnProperty(nodeId) ? panel.colors[nodeId] : "");
      }
    }
  }

  // Paints whichever highlight is currently on, without touching the buttons. A
  // panel that finished loading after the user pressed one has just drawn its nodes
  // in their ordinary diff colours, so the highlight has to be laid over it again -
  // otherwise the button reads "Hide Cycles" while one panel shows none.
  function reapplyHighlight(){
    if(isCycleShown) { showCycles(); }
    if(isLoopShown) { showLoops(); }
  }

  // mcritweb: issue #69 - loop boundaries.
  //
  // "Show Loop Boundaries" was inert on this page. loopify_dagre draws them on the
  // single-graph page, but it cannot be reused here: it is a singleton over the
  // globals `dotFile` and `loopsObj`, it rewrites the dot graph into a layout of its
  // own that this page deliberately does not render (`modifiedDotFile_a = dotFile_a`
  // above), and it appends its one `#bgFill` to a `#graphContainer g.zoom` that does
  // not exist here. What the control actually shows is a filled hull per loop, so
  // that is what these draw - per panel, from the panel's own loops and its own
  // rendered blocks.

  // Fill per nesting depth. loopify_dagre's palette, so the two views read alike -
  // and the first of these is the swatch on the checkbox in the template.
  var loopBgColors = ['#fdd0a2', '#fdae6b', '#fd8d3c', '#f16913', '#d94801', '#8c2d04'];

  // How far outside the blocks the hull is drawn, in layout units. loopify_dagre
  // hulls the block corners exactly, which leaves the fill showing only in the gaps
  // between blocks; a margin is what makes it read as a boundary around the loop.
  var loopBoundaryPadding = 12;

  // Nesting depth of every loop in a /explore/findLoops/ response. The detector
  // sorts loops by size and gives each the index of the smallest loop containing it
  // as "parent" ("" for an outermost one), so the depth is the length of that chain.
  function loopDepths(loops){
    var depths = [];
    for(var i=0; i<loops.length; i++) {
      var depth = 0;
      var at = i;
      // parent indices are strictly increasing by construction, so this terminates -
      // but the response comes off the wire, so bound the walk rather than trust it
      while(depth < loops.length) {
        var parent = loops[at] ? loops[at].parent : "";
        if(parent === "" || parent === null || parent === undefined) { break; }
        parent = parseInt(parent, 10);
        if(isNaN(parent) || parent < 0 || parent >= loops.length || parent === at) { break; }
        at = parent;
        depth++;
      }
      depths.push(depth);
    }
    return depths;
  }

  // The four corners of one block, in its panel's layout coordinates, grown by the
  // padding. Mirrors loopify_dagre's getRectanglePoints, but reads the panel's own
  // node dict rather than the shared one.
  function blockCorners(panel, nodeId){
    if(!panel.nodes.hasOwnProperty(nodeId)) { return []; }
    var node = panel.nodes[nodeId];
    var rect = node.select("rect");
    if(rect.empty()) { return []; }
    var translate = d3.transform(node.attr("transform")).translate;
    var x = Number(rect.attr("x")) + translate[0] - loopBoundaryPadding;
    var y = Number(rect.attr("y")) + translate[1] - loopBoundaryPadding;
    var width = Number(rect.attr("width")) + 2 * loopBoundaryPadding;
    var height = Number(rect.attr("height")) + 2 * loopBoundaryPadding;
    return [
      {x: x, y: y},
      {x: x + width, y: y},
      {x: x, y: y + height},
      {x: x + width, y: y + height}
    ];
  }

  // Draws one panel's loop boundaries. Call after its showGraph(), which is what
  // fills panel.nodes with the rendered blocks these are measured from.
  function renderLoopBoundaries(key){
    var panel = cfgPanels[key];
    var svg = d3.select("#graphContainer_" + key);
    // the group dagre rendered into, which is also the one zoom transforms - so the
    // boundaries pan and scale with the graph instead of drifting off it
    var inner = svg.select("g");
    if(inner.empty()) { return; }

    svg.select("#bgFill_" + key).remove();
    var group = inner.append("g")
      .attr("id", "bgFill_" + key)
      .attr("class", "bgFill");
    // first child, so the fills sit behind the blocks and edges rather than over them
    group.node().parentNode.insertBefore(group.node(), group.node().parentNode.firstChild);

    var depths = loopDepths(panel.loops);
    var order = [];
    for(var i=0; i<panel.loops.length; i++) { order.push(i); }
    // outermost first, so a nested loop's fill lands on top of the one containing it
    order.sort(function(x, y){ return depths[x] - depths[y]; });

    for(var j=0; j<order.length; j++) {
      var loop = panel.loops[order[j]];
      var blocks = (loop && loop.nodes) ? loop.nodes : [];
      var points = [];
      for(var k=0; k<blocks.length; k++) {
        Array.prototype.push.apply(points, blockCorners(panel, blocks[k]));
      }
      var hull = points.length ? convexHull(points) : [];
      // a loop whose blocks the renderer dropped, or that came back collinear, has
      // no area to fill - skip it rather than emit a degenerate path
      if(hull.length < 3) { continue; }
      var d = "M " + hull[0].x + " " + hull[0].y;
      for(var p=1; p<hull.length; p++) {
        d += " L " + hull[p].x + " " + hull[p].y;
      }
      group.append("path")
        .attr("fill", loopBgColors[depths[order[j]] % loopBgColors.length])
        .attr("stroke", "none")
        .attr("d", d + " Z");
    }

    // the checkbox may have been unticked while this panel was still loading
    setLoopBoundaryVisibility();
  }

  function setLoopBoundaryVisibility(){
    // one group per panel here, rather than the single #bgFill the one-graph page has
    d3.selectAll("g.bgFill").style("display", isLoopBoundaryShown ? null : "none");
  }

  // The two highlights paint the same rects, so at most one of them is on.
  function setHighlightMode(mode){
    resetNodeFills();
    isCycleShown = (mode == "cycles");
    isLoopShown = (mode == "loops");
    if(isCycleShown) { showCycles(); }
    if(isLoopShown) { showLoops(); }
    d3.select("#showCycles").attr("value", isCycleShown ? "Hide Cycles" : "Show Cycles");
    d3.select("#showLoops").attr("value", isLoopShown ? "Hide Loops" : "Show Loops");
  }

  function scrollToNode(node){

    if(isHoverOnLeftPanel){
      return;
    }

    var svg = d3.select("#graphContainer");
    var inner = svg.select("g");
    var transform = d3.transform(inner.attr("transform"));
    var scale = transform.scale[0];
    var translateX = transform.translate[0];
    var translateY = transform.translate[1];

    var svgWidth = svg.node().clientWidth;
    var svgHeight = svg.node().clientHeight;

    var nodeTransform = d3.transform(node.attr("transform"));
    var nodetX = nodeTransform.translate[0];
    var nodetY = nodeTransform.translate[1];

    var rect = node.select("rect");
    nodetX += +rect.attr("x");
    nodetY += +rect.attr("y");

    nodetX *= -scale;
    nodetY *= -scale;

    nodetX += svgWidth/2.0;
    nodetY += svgHeight/8.0;  // Shift it an eighth of the height down instead of half the height of panel

    // inner.attr("transform", "translate("+ nodetX + "," + nodetY + ") scale(" + scale + ")");
    zoom
      .translate([nodetX , nodetY])
      .event(svg);

  }

  function saveTextAsFile(textToSave) {
    var textToSaveAsBlob = new Blob([textToSave], {type:"text/plain"});
    var textToSaveAsURL = window.URL.createObjectURL(textToSaveAsBlob);
    var fileNameToSaveAs = "outfile.svg";
 
    var downloadLink = document.createElement("a");
    downloadLink.download = fileNameToSaveAs;
    downloadLink.innerHTML = "Download File";
    downloadLink.href = textToSaveAsURL;
    downloadLink.onclick = destroyClickedElement;
    downloadLink.style.display = "none";
    document.body.appendChild(downloadLink);
 
    downloadLink.click();
  }

  function destroyClickedElement(event) {
    document.body.removeChild(event.target);
  }

  // Translate to the given point
  function translateTo(cx, cy){

    var svg = d3.select("#graphContainer");
    var inner = svg.select("g");
    var transform = d3.transform(inner.attr("transform"));
    var scale = transform.scale[0];
    var translateX = transform.translate[0];
    var translateY = transform.translate[1];

    var svgWidth = svg.node().clientWidth;
    var svgHeight = svg.node().clientHeight;

    
    var tX = -cx*scale;
    var tY = -cy*scale;

    tX += svgWidth/2.0;
    tY += svgHeight/2.0; 

    // inner.attr("transform", "translate("+ tX + "," + tY + ") scale(" + scale + ")");
    zoom
      .translate([tX , tY])
      .event(svg);

  }

  Set.prototype.intersection = function(setB) {
    var intersection = new Set();
    for (var elem of setB) {
        if (this.has(elem)) {
            intersection.add(elem);
        }
    }
    return intersection;
  }

  function escapeRegExp(str) {
    return str.replace(/[\-\[\]\/\{\}\(\)\*\+\?\.\\\^\$\|]/g, "\\$&");
  }

  // mcritweb: issue #69 - the three taint highlighters build a coloured span around a
  // line of block text and assign the result into innerHTML. The markup is the
  // highlight, so those three cannot become .text() calls the way the tooltip did; the
  // untrusted half goes through here instead. Block text comes out of the dot graph,
  // which carries the api names smda read out of the analysed binary.
  // tests/testScriptEscaping.py fails on any interpolation into a span that skips it.
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }


  
// }