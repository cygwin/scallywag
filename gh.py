#!/usr/bin/env python3

import contextlib
import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import carpetbag
import gh_token


class Backend():
    @staticmethod
    def cancel_build(bbid):
        _github_workflow_cancel(bbid)

    @staticmethod
    def request_build(package, maintainer, commit, reference, default_tokens, buildnumber):
        with locked():
            bbid, buildurl = _github_workflow_trigger(package, maintainer, commit, reference, default_tokens, buildnumber)
        return bbid, buildurl

    @staticmethod
    def check_build_status(bbid):
        return _github_check_status(bbid)


@contextlib.contextmanager
def locked():
    old_umask = os.umask(0o000)
    lockfile = open('/tmp/scallywag.request_build.lock', 'w+')
    os.umask(old_umask)
    fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
    logging.info("acquired request_build lock")
    try:
        yield lockfile
    finally:
        logging.info("releasing request_build lock")
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)
        lockfile.close()


def _github_workflow_trigger(package, maintainer, commit, reference, default_tokens, buildnumber):
    # strip out any over-quoting in the token, as it's harmful to passing the
    # client_payload into scallywag via the command line
    default_tokens = re.sub(r'[\'"]', r'', default_tokens)

    payload = {
        "BUILDNUMBER": buildnumber,
        "PACKAGE": package,
        "MAINTAINER": maintainer,
        "COMMIT": commit,
        "REFERENCE": reference,
        "DEFAULT_TOKENS": default_tokens,
    }

    data = {
        "ref": "master",
        "inputs": {
            "title": "(%s) %s" % (buildnumber, package),  # appears as the run name in UI
            "payload": json.dumps(payload)},
    }

    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/%s/scallywag/actions/workflows/scallywag.yml/dispatches' % owner)

    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('X-GitHub-Api-Version', '2026-03-10')

    try:
        response = urllib.request.urlopen(req, data=json.dumps(data).encode('utf-8'))
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 200:
        print('scallywag: GitHub REST API failed status %s' % (status))
        return -1, None

    resp = response.read().decode('utf-8')
    j = json.loads(resp)

    wfr_id = j["workflow_run_id"]
    buildurl = j["html_url"]

    logging.info("jobs dispatched with wfr_id %d" % (wfr_id))
    return wfr_id, buildurl


def _github_workflow_cancel(wfr_id):
    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/{}/scallywag/actions/runs/{}/cancel'.format(owner, wfr_id), method='POST')

    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + token)

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 202:
        print('scallywag: GitHub REST API failed status %s' % (status))


def _github_check_status(wfr_id):
    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/{}/scallywag/actions/runs/{}'.format(owner, wfr_id))

    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + token)

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 200:
        logging.error('scallywag: GitHub REST API failed status %s' % (status))
        return None

    j = json.loads(response.read().decode('utf-8'))

    u = process_wfr(j)
    if not hasattr(u, 'buildnumber'):
        logging.error('no buildnumber in %s' % (j))
        return None

    return u


def process_wfr(wfr):
    u = carpetbag.Update()

    u.backend_id = wfr['id']
    u.buildurl = wfr['html_url']
    u.duration = parse_iso8601_time(wfr['updated_at']) - parse_iso8601_time(wfr['created_at'])

    # extract build_id from the title
    title = wfr['display_title']
    match = re.search(r'\((.*)\)', title)
    if match:
        u.buildnumber = int(match.group(1))

    conclusion = wfr['conclusion']
    if conclusion is None:  # no conclusion => still running
        u.status = 'pending'
    elif conclusion == 'success':
        u.status = 'build succeeded'
    elif conclusion == 'cancelled':
        u.status = 'cancelled'
    else:
        # action_required, failure, neutral, skipped, stale, timed_out, startup_failure
        u.status = 'build failed'

    logging.info('github, backend_id: %d, conclusion: %s -> status: %s' % (u.backend_id, conclusion, u.status))

    return u


def parse_iso8601_time(s):
    time_format = '%Y-%m-%dT%H:%M:%SZ'  # e.g. "2021-05-27T20:38:23Z"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


def examine_run_artifacts(wfr_id, u):
    # Retrieve list of workflow run artifacts
    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/{}/scallywag/actions/runs/{}/artifacts'.format(owner, wfr_id))
    req.add_header('Accept', 'application/vnd.github.v3+json')

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    logging.info("artifacts REST API status %s" % status)
    if status != 200:
        return False

    u.artifacts = {}
    found_metadata = False

    j = json.loads(response.read().decode('utf-8'))

    for a in j['artifacts']:
        # ignore builddir artifacts
        if 'builddir' in a['name']:
            continue

        # extract metadata we need from metadata artifact
        if a['name'] == 'metadata':
            url = a['archive_download_url']
            req = urllib.request.Request(url)
            req.add_unredirected_header('Authorization', 'Bearer ' + token)

            # occasionally, the metadata file is 404, despite appearing in the
            # list of artifacts. it seems we need to wait a little while after
            # the run has completed before that URL becomes valid, so we'll try
            # again later.
            try:
                response = urllib.request.urlopen(req)
            except urllib.error.URLError as e:
                logging.info("metadata download REST API response %s" % e)
                break

            # fetch to a temporary file as zipfile needs to seek
            with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
                shutil.copyfileobj(response, tmpfile)

            with zipfile.ZipFile(tmpfile.name) as z:
                with z.open('scallywag.json') as m:
                    mj = json.load(m)
                    u.buildnumber = mj['BUILDNUMBER']
                    u.package = mj['PACKAGE']
                    u.commit = mj['COMMIT']
                    u.reference = mj['REFERENCE']
                    u.maintainer = mj['MAINTAINER']
                    u.tokens = mj['TOKENS']
                    u.announce = mj['ANNOUNCE']

            # remove tmpfile
            os.remove(tmpfile.name)

            found_metadata = True

            continue

        # note package collection artifacts
        if a['name'].endswith('packages'):
            arch = a['name'][:-len('packages')].strip()
            arch = arch.replace('i686', 'x86')
            u.artifacts[arch] = a['archive_download_url']

    # if we couldn't retrieve, or didn't find the metadata file in the workflow
    # artifacts, try again later
    return found_metadata


if __name__ == '__main__':
    import sys
    import types

    u = types.SimpleNamespace()
    examine_run_artifacts(sys.argv[1], u)
    print(u)
