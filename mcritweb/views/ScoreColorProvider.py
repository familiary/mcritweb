#: The colour a tint is mixed into, per theme, matching `--page-bg` in style.css.
#: The maps below are drawn for a white page: every score is a hue laid over the
#: ground at 40% and "no score" is the ground itself, so on a dark page the same
#: arithmetic paints the whole table in near-white blocks and every cell that
#: matched nothing brightest of all - the heat map reads inverted. Only the ground
#: changes; the hues, the thresholds and the scores behind them do not. Score to
#: colour is presentation and may change here, per AGENTS.md; scores may not.
THEME_GROUNDS = {
    "light": (0xff, 0xff, 0xff),
    "dark": (0x1a, 0x1d, 0x20),
}

#: How much of the requested opacity a theme actually spends. A tint that is legible
#: on white at 40% is a mid-grey on black, and the text on it - white, on a dark page
#: - then sits at 3.6:1. Spending 70% of it keeps the worst step of the map (yellow)
#: above 6:1 while leaving the ordering of the scale untouched.
THEME_TINT_STRENGTHS = {
    "light": 1.0,
    "dark": 0.7,
}


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
        opacity = opacity * self.tint_strength
        return "".join([f"{int(g + opacity * (e - g)):02x}" for e, g in zip(tup, self.ground)])

    def _groundHex(self):
        """What a cell with nothing to report is painted: the page it sits on."""
        return "".join([f"{g:02x}" for g in self.ground])

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
        return self._groundHex()

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
        return self._groundHex()

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
        return self._groundHex()

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
        return self._groundHex()

    def __init__(self, theme="light") -> None:
        self.ground = THEME_GROUNDS.get(theme, THEME_GROUNDS["light"])
        self.tint_strength = THEME_TINT_STRENGTHS.get(theme, THEME_TINT_STRENGTHS["light"])