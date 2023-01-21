#!/usr/bin/env python3
#
# start a package build via backend API
#

import contextlib
import fcntl
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

import carpetbag
import gh_token
import appveyor_token


@contextlib.contextmanager
def locked():
    old_umask = os.umask(0o000)
    lockfile = open('/tmp/scallywag.request_build.lock', 'w+')
    os.umask(old_umask)
    fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
    try:
        yield lockfile
    finally:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)
        lockfile.close()


def _appveyor_build_request(package, maintainer, commit, reference, default_tokens, buildnumber):
    slug = 'scallywag'

    account, token = appveyor_token.fetch_token()

    data = {
        "accountName": account,
        "projectSlug": slug,
        "branch": "master",
        "environmentVariables": {
            "BUILDNUMBER": buildnumber,
            "PACKAGE": package,
            "MAINTAINER": maintainer,
            "COMMIT": commit,
            "REFERENCE": reference,
            "DEFAULT_TOKENS": default_tokens,
        }
    }

    req = urllib.request.Request('https://ci.appveyor.com/api/builds')

    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    req.add_header('Authorization', 'Bearer ' + token)

    try:
        response = urllib.request.urlopen(req, json.dumps(data).encode('utf-8'))
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 200:
        print('scallywag: AppVeyor REST API failed status %s' % (status))
        return -1

    j = json.loads(response.read().decode('utf-8'))
    return j['buildId']


def _github_most_recent_wfr_id():
    data = {
        "event": "repository_dispatch",
        "per_page": 1
    }

    qs = urllib.parse.urlencode(data)

    req = urllib.request.Request('https://api.github.com/repos/cygwin/scallywag/actions/runs' + '?' + qs)
    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

    try:
        response = urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        response = e

    status = response.getcode()
    if status != 200:
        print('scallywag: GitHub REST API failed status %s' % (status))
        return 0, None

    j = json.loads(response.read().decode('utf-8'))

    wfr = j['workflow_runs']
    if len(wfr) <= 0:
        return 0, None

    return wfr[0]['id'], wfr[0]['html_url']


def _github_workflow_trigger(package, maintainer, commit, reference, default_tokens, buildnumber):
    prev_wrf_id, _ = _github_most_recent_wfr_id()

    # strip out any over-quoting in the token, as it's harmful to passing the
    # client_payload into scallywag via the command line
    default_tokens = re.sub(r'[\'"]', r'', default_tokens)

    data = {
        "event_type": "(%s) %s" % (buildnumber, package),  # use this just because it appears as the run name in UI
        "client_payload": {
            "BUILDNUMBER": buildnumber,
            "PACKAGE": package,
            "MAINTAINER": maintainer,
            "COMMIT": commit,
            "REFERENCE": reference,
            "DEFAULT_TOKENS": default_tokens,
        }
    }

    req = urllib.request.Request('https://api.github.com/repos/cygwin/scallywag/dispatches')

    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

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

        if wfr_id != prev_wrf_id:
            return wfr_id, buildurl

        time.sleep(1)

    print('scallywag: timeout waiting for GitHub to assign a wrf_id')
    print('scallywag: PLEASE REPORT THIS!')

    return 0, None


def request_build(commit, reference, package, maintainer, tokens=''):
    default_tokens = ''
    try:
        with open(os.path.join('/sourceware/cygwin-staging/home', maintainer, '!scallywag')) as f:
            default_tokens = ''.join([l.strip() for l in f.readlines()])
    except FileNotFoundError:
        pass

    if tokens:
        default_tokens = default_tokens + ' ' + tokens

    if 'disable' in default_tokens:
        print('scallywag: disabled by you')
        return

    if 'nobuild' in default_tokens:
        print('scallywag: not building due to nobuild')
        return

    # record job as requested and generate buildnumber
    with sqlite3.connect(carpetbag.dbfile) as conn:
        cursor = conn.execute('INSERT INTO jobs (srcpkg, hash, ref, user, status, tokens) VALUES (?, ?, ?, ?, ?, ?)',
                              (package, commit, reference, maintainer, 'requested', tokens))
        buildnumber = cursor.lastrowid
        conn.commit()
    conn.close()

    # request job
    if 'appveyor' in default_tokens:
        backend = 'appveyor'
        bbid = _appveyor_build_request(package, maintainer, commit, reference, default_tokens, buildnumber)
        buildurl = None
    else:
        backend = 'github'
        with locked():
            bbid, buildurl = _github_workflow_trigger(package, maintainer, commit, reference, default_tokens, buildnumber)

    # an error occurred requesting the job
    if bbid < 0:
        return

    print('scallywag: build {0} queued on {1}'.format(buildnumber, backend))
    print('scallywag: https://cygwin.com/cgi-bin2/jobs.cgi?id={0}'.format(buildnumber))

    # record job as pending
    now = time.time()
    with sqlite3.connect(carpetbag.dbfile) as conn:
        conn.execute('UPDATE jobs SET status = ?, logurl = ?, timestamp = ?, backend = ?, backend_id = ? WHERE id = ?',
                     ('pending', buildurl, now, backend, bbid, buildnumber))
        conn.commit()
    conn.close()
