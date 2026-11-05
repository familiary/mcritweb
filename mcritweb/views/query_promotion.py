"""Promote the verified local input of a query, shared by UI and API."""

import hashlib
import json
import os

from flask import current_app
from mcrit.queue.QueueRemoteCalls import to_binary as canonicalise_queue_json
from smda.common.SmdaReport import SmdaReport

from mcritweb.views.utility import query_upload_path


class PromotionError(ValueError):
    """A refused promotion. `job_id` is the backend's id for the job, once one was
    found, so a caller can link to it rather than to the id as it was typed."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
        self.job_id = None


def promote(client, job_id, family='', version=''):
    from mcritweb.views import data
    job = client.getJobData(job_id.lower()) if data.JOB_ID_PATTERN.fullmatch(job_id) else None
    if job is None:
        raise PromotionError("The given Job ID doesn't exist", 404)
    try:
        return promote_job(client, job, family, version)
    except PromotionError as error:
        error.job_id = job.job_id
        raise


def promote_job(client, job, family, version):
    from mcritweb.views import data
    if job.method not in data.QUERY_UPLOAD_KINDS:
        raise PromotionError('Only a query can be promoted to a sample.')
    path = query_upload_path(current_app, job.job_id)
    if path is None:
        raise PromotionError('This job cannot be promoted.')
    info = data.query_report_sample_info(client, job)
    sha256 = info.get('sha256')
    if not isinstance(sha256, str) or not data.SMDA_SHA256.fullmatch(sha256):
        raise PromotionError('The report of this query does not record which file it was run for, so it cannot be promoted.')
    sample = client.getSampleBySha256(sha256.lower())
    if sample is not None:
        return sample, None, True
    if not os.path.isfile(path):
        raise PromotionError('The file this query was run for is no longer available on this server, so it cannot be promoted. Please submit it again.', 410)
    family, version = family.strip(), version.strip()
    for name, value in (('family', family), ('version', version)):
        if not data.PROMOTION_METADATA.fullmatch(value):
            raise PromotionError(f'The {name} may only contain up to 64 letters, digits, spaces, or any of ". _ -".')
    with open(path, 'rb') as source:
        content = source.read()
    kind = data.QUERY_UPLOAD_KINDS[job.method]
    report = None
    digest = None
    if kind == 'smda':
        try:
            report = SmdaReport.fromDict(json.loads(content))
            digest = hashlib.sha256(canonicalise_queue_json(report.toDict())).hexdigest()
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            current_app.logger.warning('Could not read the stored SMDA report of job %s', job.job_id)
    else:
        digest = hashlib.sha256(content).hexdigest()
    expected = data.query_payload_sha256(job)
    if digest is None or expected is None or digest != expected:
        raise PromotionError('The stored copy of this query no longer matches it, so it was not promoted.', 409)
    if kind == 'smda':
        if family:
            report.family = family
        if version:
            report.version = version
        added = client.addReport(report)
        if not isinstance(added, tuple) or not added or added[0] is None:
            raise PromotionError('The sample could not be added to the database.', 502)
        return added[0], added[1], False
    address, bitness = None, None
    if kind == 'dumped':
        address = data.query_report_base_address(info)
        bitness = info.get('bitness')
        if address is None or bitness not in (32, 64):
            raise PromotionError('The report of this query no longer records how the dump was mapped, so it cannot be promoted.')
    new_job = client.addBinarySample(content, family=family or None, version=version or None,
                                     is_dump=kind == 'dumped', base_addr=address, bitness=bitness)
    if not new_job:
        raise PromotionError('The sample could not be added to the database.', 502)
    return None, new_job, False
