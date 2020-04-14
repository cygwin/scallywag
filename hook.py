#!/usr/bin/env python3

import cgitb
import json
import logging
import os
import re
import sqlite3
import sys
import time

dbfile = '/sourceware/cygwin-staging/scallywag/carpetbag.db'


def parse_time(s):
    time_format = '%m/%d/%Y %I:%M %p'  # e.g. "8/17/2019 4:41 PM"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


def hook():
    if os.environ['REQUEST_METHOD'] != 'POST':
        return '400 Bad Request'

    # request originates from appveyor, or me
    if os.environ['REMOTE_ADDR'] not in ['138.91.141.243', '86.158.32.4']:
        return '403 Forbidden'

    if 'SCALLYWAG_AUTH' not in os.environ:
        return '401 Unauthorized'

    auth = os.environ.get('HTTP_AUTHORIZATION', '')
    auth = re.sub('^Basic ', '', auth)
    if auth != os.environ.get('SCALLYWAG_AUTH'):
        return '401 Unauthorized'

    data = sys.stdin.read()
    j = json.loads(data)
    with open('/sourceware/cygwin-staging/scallywag/last.json', 'w') as f:
        print(json.dumps(j, sort_keys=True, indent=4), file=f)

    buildnumber = j['eventData']['buildNumber']
    buildurl = j['eventData']['buildUrl']
    passed = j['eventData']['passed']
    started = parse_time(j['eventData']['started'])
    finished = parse_time(j['eventData']['finished'])
    artifacts = {}
    arches = []

    for job in j['eventData']['jobs']:
        messages = job['messages']
        message = messages[0]['message']

        evars = {i[0]: i[1] for i in map(lambda m: m.split(': ', 1), message.split('; '))}
        package = evars['PACKAGE']
        commit = evars['COMMIT']
        arch = evars['ARCH'].replace('i686', 'x86')
        maintainer = evars['MAINTAINER']

        if arch != 'skip':
            arches.append(arch)
            if len(job['artifacts']):
                artifacts[arch] = job['artifacts'][0]['permalink']

    arches = ' '.join(sorted(arches))
    logging.info('buildno: %d, passed %s, package: %s, commit: %s, arches: %s' % (buildnumber, passed, package, commit, arches))

    with sqlite3.connect(dbfile) as conn:
        conn.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (buildnumber, package, commit, maintainer, 'succeeded' if passed else 'failed', buildurl, started, finished, arches))
        conn.commit()

    return '200 OK'


if __name__ == '__main__':
    with sqlite3.connect(dbfile) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id integer primary key, srcpkg text, hash text, user text, status text, logurl text, start_timestamp integer, end_timestamp integer, arches text)''')

    cgitb.enable(format='text')
    try:
        print('Status: %s' % hook())
        print()
    except BaseException:
        # allow cgitb to do it's thing
        print('Content-Type: text/plain')
        print('Status: 422')
        print()
        raise
