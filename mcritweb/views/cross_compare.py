import json


def get_sample_to_job_id(job_info):
    return json.loads(job_info.payload["params"])['0']


#: The orderings the cross compare matrix can be asked for by name. They are resolved
#: server-side rather than spelled out as a `?custom=` id list, because that list grows
#: with the job - a cross compare over a couple of thousand samples is a query string of
#: several kilobytes - and because a named order survives a family being renamed.
CROSS_ORDERINGS = ("clustered", "sample_id", "family")


def order_sample_ids(samples, ordering, clustered_sequence=None):
    """ The sample ids of the given SampleEntries in one of CROSS_ORDERINGS """
    if ordering == "sample_id":
        return [sample.sample_id for sample in sorted(samples, key=lambda sample: sample.sample_id)]
    if ordering == "family":
        return [sample.sample_id for sample in sorted(samples, key=family_sort_key)]
    # "clustered" is whatever the backend computed, and the only ordering we cannot derive
    return clustered_sequence


def family_sort_key(sample):
    """ Group a sample with its family, newer versions last. Versions compare as strings.

    Both fields come out of an analysed binary and are whatever the backend stored, so
    they are coerced rather than trusted to be strings - a sort that raises would take
    the whole result page with it. """
    return (str(sample.family or "").lower(), str(sample.version or "").lower(), sample.sample_id)


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