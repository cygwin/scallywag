#!/usr/bin/env python3

import cgitb
import hashlib
import hmac
import json
import os
import sys
import time
import traceback

import carpetbag


basedir = os.path.dirname(os.path.realpath(__file__))
secretfile = os.path.join(basedir, 'secret')


def parse_iso8601_time(s):
    time_format = '%Y-%m-%dT%H:%M:%SZ'  # e.g. "2021-05-27T20:38:23Z"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


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

    u.backend_id = wfr['id']
    u.buildurl = wfr['html_url']
    u.duration = parse_iso8601_time(wfr['updated_at']) - parse_iso8601_time(wfr['created_at'])

    if wfr['conclusion'] == 'success':
        u.status = 'succeeded'
    elif wfr['conclusion'] == 'cancelled':
        u.status = 'cancelled'
    else:
        u.status = 'failed'

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
        carpetbag.update_status(u)

    return '200 OK', ''


def test():
    with open(os.path.join(basedir, 'last.json')) as f:
        data = f.read()
    u = process(data)
    if u:
        carpetbag.update_status(u)


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
