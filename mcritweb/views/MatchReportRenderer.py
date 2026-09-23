import json
import logging
import math
import os
import sys
from collections import defaultdict

from mcrit.storage.MatchingResult import MatchingResult
from PIL import Image

from mcritweb.views.client import get_client

LOG = logging.getLogger(__name__)


def load_cached_result(result_filepath):
    result_json = {}
    matching_result = None
    with open(result_filepath) as fin:
        result_json = json.load(fin)
    if result_json:
        matching_result = MatchingResult.fromDict(result_json)
    return matching_result


class MatchReportRenderer:

    frequency_color_map = {
        # white
        0 : (0xff, 0xff, 0xff),
        # blue
        1: (0x10, 0x7f, 0xfc),
        # light blue
        2: (0x22, 0xfe, 0xfd),
        # green
        3: (0x1f, 0xfe, 0x28),
        # green yellow: (0xc1, 0xfe. 0x2f)
        # yellow
        4: (0xff, 0xff, 0x35),
        # light orange: (0xfe, 0xc1, 0x2c)
        # orange
        5: (0xfe, 0x82, 0x25),
        # light red:
        #6: (0xfd, 0x46, 0x21),
        # red
        6: (0xfd, 0x1a, 0x20),
        # violet
        7: (0xb4, 0x00, 0xff)
    }
    matching_color_map_50 = {
        # white
        0: (0xff, 0xff, 0xff),
        # dark blue
        1: (0x00, 0x45, 0xba),
        # blue
        2: (0x00, 0x80, 0xff),
        # light blue
        3: (0x22, 0xfe, 0xfd),
        # green
        4: (0x1f, 0xfe, 0x28),
        # yellow
        5: (0xff, 0xff, 0x35),
        # orange
        6: (0xfe, 0x82, 0x25),
        # red
        7: (0xfd, 0x1a, 0x20),
    }
    top_color_map = {
        "b": (0x10, 0x7f, 0xfc),
        "g": (0x1f, 0xfe, 0x28),
        "r": (0xfd, 0x1a, 0x20),
        "bg": (0x22, 0xfe, 0xfd),
        "br":  (0xb4, 0x00, 0xff),
        "gr": (0xff, 0xff, 0x35),
        "bgr": (0x99, 0x99, 0x99)
    }

    def loadReportFromFile(self, filepath):
        self.match_report = load_cached_result(filepath)
        self.processReport(self.match_report)


    def processReport(self, match_report):
        self.match_report = match_report
        client = get_client()
        self.sample_info = self.match_report.reference_sample_entry
        self.sample_infos = {matched_sample.sample_id: matched_sample for matched_sample in self.match_report.getSampleMatches()}
        if client.isSampleId(self.sample_info.sample_id):
            self.function_infos = {function_info.function_id: function_info for function_info in client.getFunctionsBySampleId(self.match_report.reference_sample_entry.sample_id)}
        else:
            # TODO: find a way to get function_infos
            self.function_infos = {}
        for match in self.match_report.function_matches:
            # For query reports, our maps are keyed by the negated function_id, so that they line
            # up with self.function_infos. Keep this a local, display-only id: MatchingResult now
            # shares its entry objects between function_matches and filtered_function_matches,
            # so writing it back would corrupt the function_ids of the filtered report.
            function_id = -1 * match.function_id if (match_report.is_query and match.function_id > 0) else match.function_id
            # matches
            if function_id not in self.matches_by_function_id:
                self.matches_by_function_id[function_id] = []
            self.matches_by_function_id[function_id].append(match)
            # families
            if function_id not in self.function_family_match_map:
                self.function_family_match_map[function_id] = set([])
            self.function_family_match_map[function_id].add(match.matched_family_id)
            # samples
            if function_id not in self.function_sample_match_map:
                self.function_sample_match_map[function_id] = set([])
            self.function_sample_match_map[function_id].add(match.matched_sample_id)
            # libraries
            if function_id not in self.function_library_match_map:
                self.function_library_match_map[function_id] = set([])
            if match.match_is_library:
                self.function_library_match_map[function_id].add(match.matched_family_id)
        # this mapping to libraries remains regardless of report is filtered in any way
        for function_id, lib_mapping in self.match_report.library_matches.items():
            if lib_mapping:
                self.function_library_global_map[function_id] = len(set([tup[0] for tup in lib_mapping]))
        # output stats
        num_matchable_functions = sum([1 for _, function_info in self.function_infos.items() if function_info.num_instructions >= 10])
        num_matched_functions = len(set([match.function_id for match in self.match_report.function_matches]))
        # `processReport` is on the request path - data.py calls it to draw the match
        # diagram - so this cannot be a print. It stays as a debug record because it is
        # genuinely useful when a diagram comes out wrong, and it is the only place the
        # matchable/matched counts are computed together.
        LOG.debug("Sample has %d functions, %d matchable and %d with matches.",
                  len(self.function_infos), num_matchable_functions, num_matched_functions)

    def __init__(self):
        self._function_visualization_width = 1
        self.match_report = None
        self.sample_info = None
        self.sample_infos = None
        self.function_infos = None
        self.function_family_match_map = {}
        self.function_sample_match_map = {}
        self.function_library_match_map = {}
        self.function_library_global_map = {}
        self.matches_by_function_id = {}


    def _mapConfidence(self, score):
        if score > 100:
            return self.matching_color_map_50[1]
        elif score == 100:
            return self.matching_color_map_50[2]
        elif score >= 90:
            return self.matching_color_map_50[3]
        elif score >= 80:
            return self.matching_color_map_50[4]
        elif score >= 70:
            return self.matching_color_map_50[5]
        elif score >= 60:
            return self.matching_color_map_50[6]
        elif score >= 50:
            return self.matching_color_map_50[7]
        return self.frequency_color_map[0]

    def _calculateLogScore(self, cluster_size):
        if cluster_size == 0:
            return 0
        elif cluster_size == 1:
            return 1
        else:
            return 1 + int(math.log(cluster_size, 2))

    def _calculateOutputMap(self, instruction_block_size=10, num_top_cluster=3, filtered_family_id=None, filtered_sample_id=None, filtered_function_id=None):
        output_map = {}
        match_class_map = {
            0: " ",
            1: "S",
            2: "M",
            3: "FS",
            4: "FM"
        }
        cluster_by_family_id = defaultdict(set)
        this_family_id = self.sample_info.family_id
        for function_id, function_info in self.function_infos.items():
            family_matches_log_score = 0
            sample_matches_log_score = 0
            best_non_family_score = 0
            best_target_family_score = 0
            best_target_sample_score = 0
            best_score = 0
            is_matchable = function_info.num_instructions >= 10
            library_match_class = " "
            num_library_families_matched = 0
            # sample match info
            if function_id in self.function_sample_match_map:
                reduced_cluster = sorted(list(self.function_sample_match_map[function_id].difference(set([self.sample_info.sample_id]))))
                sample_matches_log_score = self._calculateLogScore(len(reduced_cluster))
            # library anf family match info
            if function_id in self.function_family_match_map:
                reduced_cluster = sorted(list(self.function_family_match_map[function_id].difference(set([this_family_id]))))
                family_matches_log_score = self._calculateLogScore(len(reduced_cluster))
                library_match_class = " "
                num_library_families_matched = len(self.function_library_match_map[function_id])
                library_match_class = match_class_map[min(num_library_families_matched, 2)]
                for family_id in reduced_cluster:
                    cluster_by_family_id[family_id].add(function_id)
            if num_library_families_matched == 0 and function_id in self.function_library_global_map:
                library_match_class = match_class_map[min(self.function_library_global_map[function_id], 2) + 2]
            # function match info
            function_matches_log_score = None
            if function_id in self.matches_by_function_id:
                function_matches_log_score = int(math.log(len(self.matches_by_function_id[function_id]), 2))
                for match in self.matches_by_function_id[function_id]:
                    is_same_family = this_family_id == match.matched_family_id
                    if not is_same_family:
                        best_non_family_score = max(best_non_family_score, match.matched_score + (1 if match.match_is_pichash else 0))
                    if match.matched_family_id == filtered_family_id:
                        best_target_family_score = max(best_target_family_score, match.matched_score + (1 if match.match_is_pichash else 0))
                    if match.matched_sample_id == filtered_sample_id:
                        best_target_sample_score = max(best_target_sample_score, match.matched_score + (1 if match.match_is_pichash else 0))
                    best_score = max(best_score, match.matched_score + (1 if match.match_is_pichash else 0))
            if filtered_function_id is not None and function_id != filtered_function_id:
                output_map[abs(function_id)] = {
                "is_matchable": is_matchable,
                "best_score": 0,
                "best_non_family_score": 0,
                "best_target_family_score": 0,
                "best_target_sample_score": 0,
                "function_matches_log_score": 0,
                "family_matches_log_score": 0,
                "sample_matches_log_score": 0,
                "library_match_class": 0,
                "most_common_cluster": [],
                "num_instructions": function_info.num_instructions,
                "num_instruction_blocks": round(function_info.num_instructions / instruction_block_size)
            }
            else:
                output_map[abs(function_id)] = {
                    "is_matchable": is_matchable,
                    "best_score": best_score,
                    "best_non_family_score": best_non_family_score,
                    "best_target_family_score": best_target_family_score,
                    "best_target_sample_score": best_target_sample_score,
                    "function_matches_log_score": function_matches_log_score,
                    "family_matches_log_score": family_matches_log_score,
                    "sample_matches_log_score": sample_matches_log_score,
                    "library_match_class": library_match_class,
                    "most_common_cluster": [],
                    "num_instructions": function_info.num_instructions,
                    "num_instruction_blocks": round(function_info.num_instructions / instruction_block_size)
                }
        # print(output_map)
        cluster_index = 0
        for family_id, function_ids in sorted(cluster_by_family_id.items(), key=lambda x: len(x[1]), reverse=True)[:num_top_cluster]:
            # print(family_id, len(function_ids))
            for function_id in function_ids:
                output_map[abs(function_id)]["most_common_cluster"].append(cluster_index)
            cluster_index += 1
        return output_map

    def _getSampleMatchScores(self):
        # TODO for some reason, we get match scores of 102 here? maybe related to how how match_is_pichash is used
        this_family_id = self.sample_info.family_id
        adjusted_score_by_sample_id = defaultdict(int)
        matchable_binweight = 0
        best_individual_match = defaultdict(int)
        best_match_function_id = defaultdict(int)
        for function_id, function_info in self.function_infos.items():
            if function_info.num_instructions < 10:
                continue
            if function_id in self.matches_by_function_id:
                reduced_cluster = sorted(list(self.function_family_match_map[function_id].difference(set([this_family_id]))))
                family_adjustment_value = 1 if len(reduced_cluster) < 3 else 1 + int(math.log(len(reduced_cluster), 2))
                # no idea what/why this is here, num_bytes is undefined?
                # if self.evaluated[function_id]["libraries"]:
                #     library_bytes_per_sample += num_bytes
                #    continue
            matchable_binweight += function_info.binweight
            samples_seen = set()
            if function_id in self.matches_by_function_id:
                for match in self.matches_by_function_id[function_id]:
                    score = (match.matched_score + match.match_is_pichash) / family_adjustment_value
                    byte_score = score * match.num_bytes / 100
                    if match.matched_sample_id not in samples_seen:
                        samples_seen.add(match.matched_sample_id)
                        adjusted_score_by_sample_id[match.matched_sample_id] += byte_score
                    if byte_score > best_individual_match[match.matched_sample_id]:
                        best_individual_match[match.matched_sample_id] = byte_score
                        best_match_function_id[match.matched_sample_id] = f"{function_id}<->{match.matched_function_id} | conf: {match.matched_score + match.match_is_pichash}"
        for sample_id, score in sorted(adjusted_score_by_sample_id.items(), key=lambda x: x[1], reverse=True):
            print(f"{sample_id:>4}: {score / matchable_binweight * 100:>5.2f} - {score:>5.0f} -- {best_individual_match[sample_id]:>5.0f} -> {self.sample_infos[sample_id].family}--- {best_match_function_id[sample_id]}")

    def _getTopClusterMapping(self, output_map):
        family_to_color = {}
        colors = ["r", "g", "b"]
        cluster_counts = defaultdict(int)
        for function_id, function_dict in output_map.items():
            for family in function_dict["most_common_cluster"]:
                cluster_counts[family] += 1
        for family, count in sorted(cluster_counts.items(), key=lambda x: (x[1], x[0]), reverse=True):
            family_to_color[family] = colors.pop()
        return family_to_color
    
    def renderText(self):
        output_map = self._calculateOutputMap()
        output_matchable = " "
        output_matches = " "
        output_match_class = " "
        output_families = " "
        for function_id, function_output in sorted(output_map.items()):
            output_matchable = "x" if function_output["is_matchable"] else " "
            output_matches += "%x" % function_output["function_matches_log_score"] if function_output["function_matches_log_score"] is not None else " "
            output_match_class += function_output["library_match_class"]
            output_families += "%x" % function_output["family_matches_log_score"] if function_output["family_matches_log_score"] is not None else " "
        print(output_matchable)
        print(output_matches)
        print(output_families)
        print(output_match_class)

    def drawBlock(self, pixels, x1, y1, block_size, color):
        for x in range(block_size):
            for y in range(block_size):
                pixels[x1 + x, y1 + y] = color

    def drawFrame(self, pixels, x1, y1, x2, y2, block_size, color):
        for xpixel in range(x1 - block_size, x2 + block_size):
            for ypixel in range(y1 - block_size, y2 + block_size):
                pixels[xpixel, ypixel] = color
                if xpixel < x1 or xpixel >= x2 or ypixel < y1 or ypixel >= y2:
                    pixels[xpixel, ypixel] = color

    def drawFamilyLegend(self, image, x, y):
        # TODO we can use this to draw boxes and scores, via ImageDraw.Draw(image).text(...)
        border_color_tuple = (0x22, 0x22, 0x22)
        pixels = image.load()
        self.drawBlock(pixels, x, y, 13, border_color_tuple)
        self.drawBlock(pixels, x + 1, y + 1, 11, self.frequency_color_map[0])

    def renderStackedDiagram(self, filtered_family_id=None, filtered_sample_id=None, filtered_function_id=None):
        background_color_tuple = (0xff, 0xff, 0xff)
        border_color_tuple = (0x22, 0x22, 0x22)
        # additional line where top X families or family clusters are highlighted in flavors of the same color?
        output_map = self._calculateOutputMap(filtered_family_id=filtered_family_id, filtered_sample_id=filtered_sample_id, filtered_function_id=filtered_function_id)
        num_matchable = sum([1 for fid, item in output_map.items() if item["is_matchable"]])
        num_blocks = sum([item["num_instruction_blocks"] for fid, item in output_map.items() if item["is_matchable"]]) + num_matchable - 1
        # determine best fitting multiple of a selected number for stack size.
        diagram_width = 2400
        num_diagrams = 3
        block_size = 9
        stack_interval = 1
        stack_size = (math.ceil(int(num_blocks / (diagram_width / (block_size + 1))) / stack_interval) + 1) * stack_interval
        num_columns = int(num_blocks / stack_size) if num_blocks % stack_size == 0 else int(num_blocks / stack_size) + 1
        LOG.debug("stack size: %s, num columns: %s", stack_size, num_columns)
        window_size_x = 40 + diagram_width
        window_size_y = 40 + num_diagrams * stack_size * block_size + 20 * (num_diagrams - 1)

        image = Image.new("RGB", (window_size_x, window_size_y), background_color_tuple)
        pixels = image.load()
        LOG.debug("drawing diagram for %s blocks, with stack size %s in %sx%s pixels.",
                  num_blocks, stack_size, window_size_x, window_size_y)
        diagram_x = 20
        diagram_y = 20
        diagram_2_y = 20 + stack_size * block_size + 20
        diagram_3_y = 20 + stack_size * block_size + 20 + stack_size * block_size + 20
        self.drawFrame(pixels, diagram_x - 1, diagram_y - 1, diagram_x + num_columns * (block_size + 1), 1 + diagram_y + stack_size * block_size, block_size, border_color_tuple)
        self.drawFrame(pixels, diagram_x - 1, diagram_2_y - 1, diagram_x + num_columns * (block_size + 1), 1 + diagram_2_y + stack_size * block_size, block_size, border_color_tuple)
        self.drawFrame(pixels, diagram_x - 1, diagram_3_y - 1, diagram_x + num_columns * (block_size + 1), 1 + diagram_3_y + stack_size * block_size, block_size, border_color_tuple)
        block_index = 0
        function_index = 0
        for function_id, function_output in sorted(output_map.items()):
            top_color_tuple = (255, 255, 255)
            # determine confidence color based on filter preferences
            bottom_color_tuple = self._mapConfidence(function_output["best_non_family_score"])
            if filtered_family_id is not None:
                bottom_color_tuple = self._mapConfidence(function_output["best_target_family_score"])
            if filtered_sample_id is not None:
                bottom_color_tuple = self._mapConfidence(function_output["best_score"])
            # determine family color based on filter preferences
            if filtered_family_id is None and filtered_sample_id is None:
                if function_output["family_matches_log_score"] is not None:
                    if function_output["family_matches_log_score"] in self.frequency_color_map:
                        top_color_tuple = self.frequency_color_map[function_output["family_matches_log_score"]]
                    else:
                        top_color_tuple = self.frequency_color_map[max(self.frequency_color_map)]
            elif filtered_family_id is not None:
                if function_output["sample_matches_log_score"] is not None:
                    if function_output["sample_matches_log_score"] in self.frequency_color_map:
                        top_color_tuple = self.frequency_color_map[function_output["sample_matches_log_score"]]
                    else:
                        top_color_tuple = self.frequency_color_map[max(self.frequency_color_map)]
            elif filtered_sample_id is not None:
                if function_output["best_target_sample_score"] > 0:
                    top_color_tuple = self.frequency_color_map[1]
            library_color_tuple = (255, 255, 255)
            if function_output["library_match_class"]:
                if function_output["library_match_class"] == "M":
                    library_color_tuple = (0xfd, 0x1a, 0x20)
                elif function_output["library_match_class"] == "S":
                    library_color_tuple = (0x1f, 0xfe, 0x28)
                elif function_output["library_match_class"] == "FM":
                    library_color_tuple = (0xfd, 0x8b, 0x8e)
                elif function_output["library_match_class"] == "FS":
                    library_color_tuple = (0x91, 0xfe, 0x95)
            if function_output["is_matchable"]:
                if function_index:
                    xindex = int(block_index / stack_size)
                    yindex = block_index % stack_size
                    x1 = diagram_x + xindex * (block_size + 1)
                    y1 = diagram_y + yindex * block_size
                    y2 = diagram_2_y + yindex * block_size
                    y3 =  diagram_3_y + yindex * block_size
                    self.drawBlock(pixels, x1, y1, block_size, border_color_tuple)
                    self.drawBlock(pixels, x1, y2, block_size, border_color_tuple)
                    self.drawBlock(pixels, x1, y3, block_size, border_color_tuple)
                    block_index += 1
                for index in range(function_output["num_instruction_blocks"]):
                    xindex = int(block_index / stack_size)
                    yindex = block_index % stack_size
                    x1 = diagram_x + xindex * (block_size + 1)
                    y1 = diagram_y + yindex * block_size
                    y2 = diagram_2_y + yindex * block_size
                    y3 = diagram_3_y + yindex * block_size
                    self.drawBlock(pixels, x1, y1, block_size, top_color_tuple)
                    self.drawBlock(pixels, x1, y2, block_size, library_color_tuple)
                    self.drawBlock(pixels, x1, y3, block_size, bottom_color_tuple)
                    block_index += 1
                function_index += 1
        for _ in range(num_blocks % stack_size):
            xindex = int(block_index / stack_size)
            yindex = block_index % stack_size
            x1 = diagram_x + xindex * (block_size + 1)
            y1 = diagram_y + yindex * block_size
            self.drawBlock(pixels, x1, y1, block_size, border_color_tuple)
            block_index += 1
        return image

    def getLibraryStats(self):
        total_count = len(self.function_infos)
        total_ins_count = 0
        single_count = 0
        single_ins_count = 0
        multi_count = 0
        multi_ins_count = 0
        library_counts = defaultdict(int)
        library_ins_counts = defaultdict(int)
        # TODO build a dict with function_id -> matched library family id?
        function_library_match_map = {}
        for match in self.match_report.function_matches:
            if match.function_id not in function_library_match_map:
                function_library_match_map[match.function_id] = set([])
            if match.match_is_library:
                function_library_match_map[match.function_id].add(match.matched_family_id)
        for function_id, function_info in self.function_infos.items():
            total_ins_count += function_info.num_instructions
            if function_info.function_id in function_library_match_map:
                if len(function_library_match_map[function_info.function_id]) == 0:
                    continue
                elif len(function_library_match_map[function_info.function_id]) == 1:
                    single_count += 1
                    single_ins_count += function_info.num_instructions
                elif len(function_library_match_map[function_info.function_id]) > 1:
                    multi_count += 1
                    multi_ins_count += function_info.num_instructions
                    for family_id in function_library_match_map[function_info.function_id]:
                        library_counts[family_id] += 1
                        library_ins_counts[family_id] += function_info.num_instructions
        return {
            "total_count": total_count,
            "total_ins_count": total_ins_count,
            "single_count": single_count,
            "single_ins_count": single_ins_count,
            "multi_count": multi_count,
            "multi_ins_count": multi_ins_count,
            "library_counts": dict(library_counts),
            "library_ins_counts": dict(library_ins_counts),
        }

    def printInfo(self):
        print("""Exponent colors:
            0     families: white
            1     family:   blue
            2-3   families: light blue
            3-7   families: green
            8-15  families: yellow
            16-31 families: orange
            32-63 families: red
            64+   families: violet
        """)
        print("Top Libraries:")
        print(self.getLibraryStats())
        self._getSampleMatchScores()


def main():
    if os.path.isfile(sys.argv[1]):
        matching_result = load_cached_result(sys.argv[1])
        report_renderer = MatchReportRenderer()
        filtered_family_id = None
        if len(sys.argv) > 2:
            filtered_family_id = int(sys.argv[2])
            matching_result.filterToFamilyId(filtered_family_id)
        report_renderer.processReport(matching_result)
        report_renderer.printInfo()
        image = report_renderer.renderStackedDiagram(filtered_family_id=filtered_family_id)
        image.show()
    else:
        print(f"Usage: {sys.argv[0]} <cached_match_report> <opt:output_filepath>")


if __name__ == "__main__":
    sys.exit(main())
