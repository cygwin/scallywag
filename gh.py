#!/usr/bin/env python3

import json
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile

import gh_token


def examine_run_artifacts(wfr_id, u):
    # Retrieve list of workflow run artifacts
    req = urllib.request.Request('https://api.github.com/repos/cygwin/scallywag/actions/runs/{}/artifacts'.format(wfr_id))
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
            req.add_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

            # occasionally, the metadata file is 404, despite appearing in the
            # list of artifacts. it seems we need to wait a little while after
            # the run has completed before that URL becomes valid, so we'll try
            # again later.
            try:
                response = urllib.request.urlopen(req)
            except urllib.error.URLError:
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
