class ScoreColorProvider:
    
    frequency_color_map = {
        # white
        0 : (0xff, 0xff, 0xff),
        # dark blue
        1: (0x00, 0x45, 0xba),
        # blue
        2: (0x10, 0x7f, 0xfc),
        # light blue
        3: (0x22, 0xfe, 0xfd),
        # green
        4: (0x1f, 0xfe, 0x28),
        # green yellow: (0xc1, 0xfe. 0x2f)
        # yellow
        5: (0xff, 0xff, 0x35),
        # light orange: (0xfe, 0xc1, 0x2c)
        # orange
        6: (0xfe, 0x82, 0x25),
        # light red:
        #6: (0xfd, 0x46, 0x21),
        # red
        7: (0xfd, 0x1a, 0x20),
        # violet
        8: (0xb4, 0x00, 0xff)
    }

    matching_color_map_100 = {
        # white
        0 : (0xff, 0xff, 0xff),
        # blue
        1: (0x00, 0x80, 0xff),
        # cyan
        2: (0x00, 0xff, 0xff),
        # green
        3: (0x00, 0xff, 0x00),
        # lime
        4: (0xc0, 0xff, 0x00),
        # yellow
        5: (0xff, 0xff, 0x00),
        # orange
        6: (0xff, 0xc0, 0x00),
        # dark orange
        7: (0xff, 0x80, 0x00),
        # red-orange
        8: (0xff, 0x40, 0x00),
        # red
        9: (0xff, 0x00, 0x00),
        # light grey
        10: (0x44, 0x44, 0x44)
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

    def _tupleToHex(self, tup, opacity=1):
        return "".join([f"{int(255 - opacity * (255 - e)):02x}" for e in tup])

    def getMatchHexColorByScore100(self, score, opacity=1):
        if score >= 90:
            return self._tupleToHex(self.matching_color_map_100[1], opacity)
        if score >= 80:
            return self._tupleToHex(self.matching_color_map_100[2], opacity)
        elif score >= 70:
            return self._tupleToHex(self.matching_color_map_100[3], opacity)
        elif score >= 60:
            return self._tupleToHex(self.matching_color_map_100[4], opacity)
        elif score >= 50:
            return self._tupleToHex(self.matching_color_map_100[5], opacity)
        elif score >= 40:
            return self._tupleToHex(self.matching_color_map_100[6], opacity)
        elif score >= 30:
            return self._tupleToHex(self.matching_color_map_100[7], opacity)
        elif score >= 20:
            return self._tupleToHex(self.matching_color_map_100[8], opacity)
        elif score >= 10:
            return self._tupleToHex(self.matching_color_map_100[9], opacity)
        elif score > 0:
            return self._tupleToHex(self.matching_color_map_100[10], opacity)
        return self._tupleToHex(self.frequency_color_map[0])

    def getMatchHexColorByScore50(self, score, opacity=1):
        if score > 100:
            return self._tupleToHex(self.matching_color_map_50[1], opacity)
        elif score == 100:
            return self._tupleToHex(self.matching_color_map_50[2], opacity)
        elif score >= 90:
            return self._tupleToHex(self.matching_color_map_50[3], opacity)
        elif score >= 80:
            return self._tupleToHex(self.matching_color_map_50[4], opacity)
        elif score >= 70:
            return self._tupleToHex(self.matching_color_map_50[5], opacity)
        elif score >= 60:
            return self._tupleToHex(self.matching_color_map_50[6], opacity)
        elif score >= 50:
            return self._tupleToHex(self.matching_color_map_50[7], opacity)
        return self._tupleToHex(self.frequency_color_map[0])

    def getFrequencyHexColorByScore(self, score, opacity=1):
        if score > 100:
            return self._tupleToHex(self.frequency_color_map[1], opacity)
        if score > 95:
            return self._tupleToHex(self.frequency_color_map[2], opacity)
        elif score >= 90:
            return self._tupleToHex(self.frequency_color_map[3], opacity)
        elif score >= 80:
            return self._tupleToHex(self.frequency_color_map[4], opacity)
        elif score >= 70:
            return self._tupleToHex(self.frequency_color_map[5], opacity)
        elif score >= 60:
            return self._tupleToHex(self.frequency_color_map[6], opacity)
        elif score >= 50:
            return self._tupleToHex(self.frequency_color_map[7], opacity)
        elif score >= 40:
            return self._tupleToHex(self.frequency_color_map[8], opacity)
        return self._tupleToHex(self.frequency_color_map[0])

    def getMatchHexColorFromResult(self, match_result, score_type, scale=100, opacity=0.4):
        if score_type not in ["matched_percent_score_weighted", "matched_percent_frequency_weighted", "matched_percent_nonlib_score_weighted", "matched_percent_nonlib_frequency_weighted", "matched_score"]:
            return "000000"
        else:
            score = getattr(match_result, score_type)
            if scale == 50:
                return self.getMatchHexColorByScore50(score, opacity=opacity)
            else:
                return self.getMatchHexColorByScore100(score, opacity=opacity)
            
    def getUniqueColorScore(self, score, opacity=0.4):
        if score is not None and score > 0:
            return self.getMatchHexColorByScore100(60, opacity=opacity)
        return "FFFFFF"

    # The result pages tint rows and cells with these rather than with the hex above. A
    # hex baked the 40% blend toward white into the page, which only works on a white
    # page: on the dark theme (#70) it was a field of near-white blocks, the empty cells
    # brightest of all. These hand the browser the hue instead and let the theme say how
    # strongly it shows and what it is mixed into (--tint-strength, --tint-ground in
    # static/style.css and theme-dark.css). On the light theme that is the same 40% into
    # white as before, give or take a rounding step of one in 255 per channel.

    def tint(self, hex_color):
        """A score colour as a CSS background. White is the maps' "no score", which paints
        the ground: a cell without a score sits in a tinted row and must not take its tint."""
        if hex_color.lower() == "ffffff":
            return "var(--tint-ground)"
        return f"color-mix(in srgb, #{hex_color} var(--tint-strength), var(--tint-ground))"

    def getMatchTintFromResult(self, match_result, score_type, scale=100):
        return self.tint(self.getMatchHexColorFromResult(match_result, score_type, scale=scale, opacity=1))

    def getMatchTintByScore100(self, score):
        return self.tint(self.getMatchHexColorByScore100(score))

    def getMatchTintByScore50(self, score):
        return self.tint(self.getMatchHexColorByScore50(score))

    def getUniqueTint(self, score):
        return self.tint(self.getUniqueColorScore(score, opacity=1))

    def __init__(self) -> None:
        pass