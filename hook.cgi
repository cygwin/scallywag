#!/usr/bin/env python3

import cgitb
import json
import os
import re
import sys
import time
import traceback

import carpetbag

basedir = os.path.dirname(os.path.realpath(__file__))
authfile = os.path.join(basedir, 'auth')


def parse_time(s):
    time_format = '%m/%d/%Y %I:%M %p'  # e.g. "8/17/2019 4:41 PM"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


def hook():
    if os.environ['REQUEST_METHOD'] != 'POST':
        return '400 Bad Request', ''

    # request originates from appveyor
    if os.environ['REMOTE_ADDR'] not in ['138.91.141.243']:
        return '403 Forbidden', ''

    if not os.path.exists(authfile):
        return '401 Unauthorized', ''
    with open(authfile) as f:
        auth = f.read().strip()

    tryauth = os.environ.get('HTTP_AUTHORIZATION', '')
    tryauth = re.sub('^Basic ', '', auth)
    if tryauth != auth:
        return '401 Unauthorized', ''

    data = sys.stdin.read()
    j = json.loads(data)
    with open(os.path.join(basedir, 'last.json'), 'w') as f:
        print(json.dumps(j, sort_keys=True, indent=4), file=f)

    buildurl = j['eventData']['buildUrl']
    passed = j['eventData']['passed']
    started = parse_time(j['eventData']['started'])
    finished = parse_time(j['eventData']['finished'])
    artifacts = {}

    for job in j['eventData']['jobs']:
        messages = job['messages']

        for m in messages:
            message = m['message']
            if 'ARCH' not in message:
                continue

            evars = {i[0]: i[1] for i in map(lambda m: m.split(': ', 1), message.split('; '))}
            buildnumber = evars['BUILDNUMBER']
            package = evars['PACKAGE']
            commit = evars['COMMIT']
            reference = evars['REFERENCE']
            arch = evars['ARCH'].replace('i686', 'x86')
            maintainer = evars['MAINTAINER']
            tokens = evars['TOKENS']

            if arch != 'skip':
                if len(job['artifacts']):
                    artifacts[arch] = job['id']

            break

    u = carpetbag.Update()
    u.buildurl = buildurl
    u.duration = finished - started
    u.status = 'succeeded' if passed else 'failed'
    u.buildnumber = buildnumber
    u.package = package
    u.commit = commit
    u.reference = reference
    u.maintainer = maintainer
    u.tokens = tokens
    u.artifacts = artifacts

    carpetbag.update_status(u)
    carpetbag.update_metadata(u)

    return '200 OK', ''


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
