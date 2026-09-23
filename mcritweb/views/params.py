"""Parsing of request parameters and of metadata encoded in uploaded filenames.

Split out of utility.py so these can be imported - and tested - without pulling in
smda, mcrit or the database layer. Nothing here touches Flask globals: each helper
takes what it works on - the request, or a job - as an argument. See issue #88.
"""

import json
import logging
import re

# slider position -> band_matches_required, as submitted to mcrit. Bijective, which
# is what lets a finished job be read back to the setting it was submitted with.
BAND_RANGE_ARG_TO_VALUE = {
    # deactivate minhash bands
    0: 0,
    # 1: "Fast"
    1: 4,
    # 1: "Standard"
    2: 2,
    # 1: "Complete"
    3: 1
}

# the labels the submit forms show for those positions - kept in sync with
# `minhash_slider_mapping` in compare.html, compare_versus.html, cross_compare.html
# and table/submit_or_query_dropzone.html
BAND_RANGE_LABELS = ["Off", "Fast", "Standard", "Complete"]

# band_matches_required -> that label.
#
# An earlier version of this comment claimed "Off" (0) and "Complete" (1) end up behaving
# identically in mcrit, citing the `band_matches_required <= 1` branch in
# MongoDbStorage._getCandidatesForMinHashesNumpy. That was wrong, and had never been
# true: MatcherInterface._getMatchesRoutineInner wraps the entire minhash stage in
# `if self._band_matches_required > 0:` (since mcrit v1.1.7, 2023), so the `<= 1` branch
# is only reached when the stage runs at all. At 0 mcrit does pichash-only matching; at 1
# it does full minhash matching and keeps every candidate. They are different modes, not
# two names for one - and collapsing the two slider positions on the strength of the old
# comment would have silently removed the pichash-only one.
BAND_VALUE_TO_LABEL = {value: BAND_RANGE_LABELS[arg] for arg, value in BAND_RANGE_ARG_TO_VALUE.items()}


def parse_band_range(request, from_form=False):
    minhash_band_range= 2
    try:
        if from_form:
            minhash_band_range = int(request.form['minhashBandRange'])
        else:
            minhash_band_range = int(request.args.get('minhashBandRange', "2"))
        minhash_band_range = min(3, minhash_band_range)
        minhash_band_range = max(0, minhash_band_range)
    except Exception:
        minhash_band_range = 2
    minhash_band_range = BAND_RANGE_ARG_TO_VALUE[minhash_band_range]
    return minhash_band_range


def get_minhash_matching_label(job_info):
    """ The MinHash matching parameter a job was submitted with, as the label the submit form showed for it, or None.

    None means "this job did not record one", which is the honest answer for jobs
    submitted before the setting existed, jobs submitted through the CLI or the API
    without it, and combineMatchesToCross jobs - a cross compare only carries the ids
    of its child getMatchesForSampleVs jobs, and the setting lives on those. Falling
    back to the server's current configuration would state a value that was never
    used for any job older than the last config change. See issue #32.

    Only band_matches_required is reported. It is the one part of the matching
    configuration mcritweb's own submit forms set - the slider in compare.html and
    friends - so a value read back from a job is a slider position the user chose.
    The other knobs (minhash_threshold, pichash_size) are reachable only through the
    API proxy in views/api.py, which forwards whatever a caller supplies; a job
    submitted that way may carry them, and this deliberately does not guess a label
    for settings no form in this application ever offered.
    """
    try:
        # the params of a job are stored as a JSON string of {index_or_name: value},
        # the same read data.result_unique_blocks does. Deliberately not routed
        # through Job.arguments, which drops the parameter names - and which raises
        # for a malformed payload, so this cannot lean on it for the lookup either.
        payload_params = json.loads(job_info.payload["params"])
        band_matches_required = payload_params["band_matches_required"]
    except Exception:
        # anything the backend stored that we cannot read back: no payload, no params,
        # no such parameter, or a payload that is not the JSON object it should be.
        # A missing label is not worth breaking a results page over.
        return None
    # a bool would otherwise pass as 1 and be labelled "Complete"
    if not isinstance(band_matches_required, int) or isinstance(band_matches_required, bool):
        return None
    # the API takes any integer, so a job can carry a value no slider position covers.
    # Report it as it is rather than rounding it to a setting nobody selected.
    return BAND_VALUE_TO_LABEL.get(band_matches_required, f"{band_matches_required} bands")


def parse_integer_query_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as int """
    param = None
    try:
        value = request.args.get(query_param)
        if value is not None:
            if value.startswith("0x"):
                param = int(value, 16)
            else:
                param = int(value)
    except Exception:
        pass
    return param

def parse_integer_list_query_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as list of int (no brackets) """
    param = None
    try:
        value = request.args.get(query_param)
        if value is not None and re.match(r"^\d+(?:[\s]*,[\s]*\d+)*$", value):
            param = [int(element.strip()) for element in value.split(',')]
    except Exception:
        pass
    return param


def parse_str_query_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as str """
    param = None
    try:
        param = request.args.get(query_param)
    except Exception:
        pass
    return param


def parse_checkbox_query_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as checkbox """
    param = False
    try:
        value = request.args.get(query_param)
        param = True if isinstance(value, str) and value.lower() in ["on", "true"] else False
    except Exception:
        pass
    return param


def parse_integer_post_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as int """
    param = None
    try:
        value = request.form.get(query_param)
        if value is not None:
            param = int(value)
    except Exception:
        pass
    return param


def parse_checkbox_post_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as checkbox """
    param = False
    try:
        value = request.form.get(query_param)
        param = True if isinstance(value, str) and value.lower() in ["on", "true"] else False
    except Exception:
        pass
    return param


def parseBaseAddrFromFilename(filename):
    # try to infer base addr from filename:
    baddr_match = re.search(re.compile("_0x(?P<base_addr>[0-9a-fA-F]{8,16})"), filename)
    if baddr_match:
        parsed_base_addr = int(baddr_match.group("base_addr"), 16)
        logging.info("Parsed base address from file name: 0x%08x %d", parsed_base_addr, parsed_base_addr)
        return parsed_base_addr
    logging.warning("No base address recognized, using None.")
    return None


def parseBitnessFromFilename(filename):
    # try to infer bitness from filename:
    baddr_match = re.search(re.compile("_0x(?P<base_addr>[0-9a-fA-F]{8,16})"), filename)
    if baddr_match:
        if len(baddr_match.group("base_addr")) > 8:
            logging.info("Parsed bitness from base addr len from file name: %s", filename)
            return 64
        else:
            return 32
    logging.warning("No base address recognized, using None.")
    return None
