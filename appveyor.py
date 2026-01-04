#!/usr/bin/env python3

import json
import urllib.error
import urllib.request

import appveyor_token


class Backend():
    @staticmethod
    def cancel_build(bbid):
        print('job cancellation not implemented for AppVeyor backend')

    @staticmethod
    def request_build(package, maintainer, commit, reference, default_tokens, buildnumber):
        bbid = _appveyor_build_request(package, maintainer, commit, reference, default_tokens, buildnumber)
        buildurl = None
        return bbid, buildurl

    @staticmethod
    def check_build_status(bbid):
        return _appveyor_check_status(bbid)


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


def _appveyor_check_status(bbid):
    return None
