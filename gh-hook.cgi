#!/usr/bin/env python3

import cgitb
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile

import carpetbag
import gh_token


basedir = os.path.dirname(os.path.realpath(__file__))
secretfile = os.path.join(basedir, 'secret')


def parse_iso8601_time(s):
    time_format = '%Y-%m-%dT%H:%M:%SZ'  # e.g. "2021-05-27T20:38:23Z"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


def examine_run_artifacts(wfr_id, u):
    # Retrieve list of workflow run artifacts
    req = urllib.request.Request('https://api.github.com/repos/cygwin/scallywag/actions/runs/{}/artifacts'.format(wfr_id))
    req.add_header('Accept', 'application/vnd.github.v3+json')

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 200:
        raise Exception(str(status))

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
            req.add_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

            # occasionally, the metadata file is 404, despite appearing in the
            # list of artifacts. it seems we need to wait a little while after
            # the run has completed before that URL becomes valid.
            for _i in range(1, 30):
                try:
                    response = urllib.request.urlopen(req)
                    break
                except urllib.error.URLError:
                    pass

                time.sleep(1)
            else:
                continue

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

            # remove tmpfile
            os.remove(tmpfile.name)

            found_metadata = True

            continue

        # note package collection artifacts
        if a['name'].endswith('packages'):
            arch = a['name'][:-len('packages')].strip()
            arch = arch.replace('i686', 'x86')
            u.artifacts[arch] = a['archive_download_url']

    # if we didn't find the metadata file in the workflow artifacts, that
    # suggests something went wrong in the build before scallywag could write
    # it...
    if not found_metadata:
        u.status = 'errored'


def process(data):
    j = json.loads(data)
    with open(os.path.join(basedir, 'last.json'), 'w') as f:
        print(json.dumps(j, sort_keys=True, indent=4), file=f)

    if j.get('action', '') != 'completed':
        return None

    # ensure this event is for the repository we are installed on
    if j.get('repository', {}).get('full_name', '') != 'cygwin/scallywag':
        return None

    wfr = j.get('workflow_run', None)
    if not wfr:
        return None

    u = carpetbag.Update()

    u.buildurl = wfr['html_url']
    u.duration = parse_iso8601_time(wfr['updated_at']) - parse_iso8601_time(wfr['created_at'])
    u.artifacts = {}

    if wfr['conclusion'] == 'success':
        u.status = 'succeeded'
    elif wfr['conclusion'] == 'cancelled':
        u.status = 'cancelled'
    else:
        u.status = 'failed'

    # examine workflow artifacts for that workflow_run id
    examine_run_artifacts(wfr['id'], u)

    return u


def hook():
    if os.environ['REQUEST_METHOD'] != 'POST':
        return '400 Bad Request', ''

    if not os.path.exists(secretfile):
        return '401 Unauthorized', ''
    with open(secretfile) as f:
        secret = f.read().strip()

    data = sys.stdin.read()

    sig = 'sha256=' + hmac.new(secret.encode(), data.encode(),
                               hashlib.sha256).hexdigest()
    trysig = os.environ.get('HTTP_X_HUB_SIGNATURE_256', '')
    if trysig != sig:
        return '401 Unauthorized', ''

    u = process(data)
    if u:
        carpetbag.update(u)

    return '200 OK', ''


def test():
    with open(os.path.join(basedir, 'last.json')) as f:
        data = f.read()
    u = process(data)
    if u:
        carpetbag.update(u)


if __name__ == '__main__':
    cgitb.enable()
    try:
        status, content = hook()
        print('Status: %s' % status)
        print()
        print(content)
    except BaseException:
        # log exception to stderr
        traceback.print_exc()
        # allow cgitb to do it's thing
        print('Content-Type: text/plain')
        print('Status: 422')
        print()
        raise
