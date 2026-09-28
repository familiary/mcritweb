import json


def get_sample_to_job_id(job_info):
    return json.loads(job_info.payload["params"])['0']


def score_to_color(score):
    """The cross-compare matrix cell for a score, as six hex digits.

    Unlike ScoreColorProvider this needs no per-theme variant: the hues are used at
    full saturation rather than mixed into the page, so they carry the same weight on
    either ground, and the two lowest steps are already near-black - which reads as
    "nothing here" against a white page and against a dark one alike.
    """
    if score >= 90:
        return "0080ff"  # dark blue
    elif score >= 80:
        return "00ffff"  # cyan
    elif score >= 70:
        return "00ff00"  # green 
    elif score >= 60:
        return "c0ff00"  # lime
    elif score >= 50:
        return "ffff00"  # yellow
    elif score >= 40:
        return "ffc000"  # orange
    elif score >= 30:
        return "ff8000"  # dark orange
    elif score >= 20:
        return "ff4000"  # red-orange
    elif score >= 10:
        return "ff0000"  # red
    elif score > 0:
        return "444444"  # light grey
    else:
        return "222222"  # dark grey / background