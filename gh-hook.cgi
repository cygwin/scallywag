#!/usr/bin/env python3

import cgitb
import hashlib
import hmac
import json
import os
import sys
import traceback

import carpetbag
import gh


basedir = os.path.dirname(os.path.realpath(__file__))
secretfile = os.path.join(basedir, 'secret')


def process(data):
    j = json.loads(data)
    with open(os.path.join(basedir, 'last.json'), 'w') as f:
        print(json.dumps(j, sort_keys=True, indent=4), file=f)

    # XXX: also handle 'requested', 'in_progress'
    if j.get('action', '') != 'completed':
        return None

    # ensure this event is for the repository we are installed on
    if j.get('repository', {}).get('full_name', '') != 'cygwin/scallywag':
        return None

    wfr = j.get('workflow_run', None)
    if not wfr:
        return None

    return gh._process_wfr(wfr)


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
        # ensure backend_id is set, if it was previously unknown due to timeout waiting for it to be assigned
        if hasattr(u, 'buildnumber'):
            carpetbag.update_backend_id(u)

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
