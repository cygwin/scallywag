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


def _github_most_recent_wfr_id():
    data = {
        "event": "repository_dispatch",
        "per_page": 1
    }

    qs = urllib.parse.urlencode(data)

    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/%s/scallywag/actions/runs?%s' % (owner, qs))
    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + token)

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    logging.info("runs REST API status %s" % status)
    if status != 200:
        logging.error('scallywag: GitHub REST API failed status %s' % (status))
        return 0, None

    resp = response.read().decode('utf-8')
    logging.info("runs REST API response %s" % resp)
    j = json.loads(resp)

    wfr = j['workflow_runs']
    if len(wfr) <= 0:
        logging.info("no most recent wrf_id available")
        return 0, None

    logging.info("most recent wrf_id %s" % wfr[0]['id'])
    return wfr[0]['id'], wfr[0]['html_url']


def _github_workflow_trigger(package, maintainer, commit, reference, default_tokens, buildnumber):
    for _i in range(1, 60):
        prev_wfr_id, _ = _github_most_recent_wfr_id()

        if prev_wfr_id != 0:
            break

        logging.info("waiting before retry")
        time.sleep(1)
    else:
        logging.info("timeout waiting for GitHub to report previous wfr_id")
        print('scallywag: timeout waiting for GitHub to report previous wfr_id')

    # strip out any over-quoting in the token, as it's harmful to passing the
    # client_payload into scallywag via the command line
    default_tokens = re.sub(r'[\'"]', r'', default_tokens)

    data = {
        "event_type": "(%s) %s" % (buildnumber, package),  # 'display_title', appears as the run name in UI
        "client_payload": {
            "BUILDNUMBER": buildnumber,
            "PACKAGE": package,
            "MAINTAINER": maintainer,
            "COMMIT": commit,
            "REFERENCE": reference,
            "DEFAULT_TOKENS": default_tokens,
        }
    }

    (owner, token) = gh_token.fetch_auth()
    req = urllib.request.Request('https://api.github.com/repos/%s/scallywag/dispatches' % owner)

    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + token)

    try:
        response = urllib.request.urlopen(req, data=json.dumps(data).encode('utf-8'))
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 204:
        print('scallywag: GitHub REST API failed status %s' % (status))
        return -1, None

    # response has no content, and doesn't give an id for the workflow that
    # we've just requested. all we can do is poll the workflow runs list and
    # guess that the most recent one is ours.
    #
    # (it seems that it takes a little while for the requested run to appear in
    # the workflow run list, with status 'queued', and then some time later it
    # changes to status 'in_progress'.)
    #
    # and since there may exist other runs with status 'in_progress', the only
    # half-way reliable way to do this is to poll until a new wfr id appears...
    #
    # see https://github.community/t/repository-dispatch-response/17950

    for _i in range(1, 60):
        wfr_id, buildurl = _github_most_recent_wfr_id()

        if wfr_id != prev_wfr_id:
            return wfr_id, buildurl

        logging.info("waiting before retry")
        time.sleep(1)

    logging.info("timeout waiting for GitHub to assign a wfr_id")
    print('scallywag: timeout waiting for GitHub to assign a wfr_id')

    return 0, None


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

    return process_wfr(j)


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

    if wfr['conclusion'] == 'success':
        u.status = 'build succeeded'
    elif wfr['conclusion'] == 'cancelled':
        u.status = 'cancelled'
    else:
        # action_required, failure, neutral, skipped, stale, timed_out, startup_failure, null
        u.status = 'build failed'

    logging.info('github, backend_id: %d, status: %s' % (u.backend_id, u.status))

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
