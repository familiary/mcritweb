import json


def get_sample_to_job_id(job_info):
    return json.loads(job_info.payload["params"])['0']


def score_to_color(score):
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


def lazy_cross_panes(samples_by_method, result_json, rendered_method):
    """What the result page needs to fill the tab of every method but rendered_method in the browser

    Rendering all six N*N matrices up front made the page grow with methods * N^2 while five
    of them stayed hidden (issue #182). The page now renders one, and a pane filled on first
    click clones its rows and cells from that one - so only what differs between the methods
    travels here: each method's sample order and, per cell in that order, the score as the
    tooltip shows it (two decimals), the match count and the colour. The colour is picked by
    score_to_color from the unrounded score, as the rendered pane does, and sent as an index
    into a shared palette rather than repeated per cell.
    """
    # insertion ordered, so list(palette) puts every colour at its index
    palette = {}

    def color_index(score):
        return palette.setdefault(score_to_color(score), len(palette))

    panes = {}
    for method, samples in samples_by_method.items():
        if method == rendered_method:
            continue
        matching_percent = result_json[method]["matching_percent"]
        matching_matches = result_json[method]["matching_matches"]
        sample_ids = [str(sample.sample_id) for sample in samples]
        panes[method] = {
            "order": [sample.sample_id for sample in samples],
            "percent": [[round(matching_percent[row_id][col_id], 2) for col_id in sample_ids] for row_id in sample_ids],
            "matches": [[matching_matches[row_id][col_id] for col_id in sample_ids] for row_id in sample_ids],
            "color": [[color_index(matching_percent[row_id][col_id]) for col_id in sample_ids] for row_id in sample_ids],
        }
    return {"palette": list(palette), "panes": panes}