#!/usr/bin/env python3

import cgitb
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time

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

    buildnumber = j['eventData']['buildNumber']
    buildurl = j['eventData']['buildUrl']
    passed = j['eventData']['passed']
    started = parse_time(j['eventData']['started'])
    finished = parse_time(j['eventData']['finished'])
    artifacts = {}
    arches = []

    for job in j['eventData']['jobs']:
        messages = job['messages']
        if not messages:
            continue
        message = messages[0]['message']

        evars = {i[0]: i[1] for i in map(lambda m: m.split(': ', 1), message.split('; '))}
        package = evars['PACKAGE']
        commit = evars['COMMIT']
        reference = evars['REFERENCE']
        arch = evars['ARCH'].replace('i686', 'x86')
        maintainer = evars['MAINTAINER']

        if arch != 'skip':
            arches.append(arch)
            if len(job['artifacts']):
                artifacts[arch] = job['id']

    arches = ' '.join(sorted(arches))
    logging.info('buildno: %d, passed %s, package: %s, commit: %s, arches: %s' % (buildnumber, passed, package, commit, arches))

    with sqlite3.connect(carpetbag.dbfile) as conn:
        cursor = conn.execute('SELECT id FROM jobs WHERE id = ?', (buildnumber,))
        if not cursor.fetchone():
            conn.execute('INSERT INTO jobs (id, srcpkg, hash, ref, user) VALUES (?, ?, ?, ?, ?)',
                         (buildnumber, package, commit, reference, maintainer))
        conn.execute('UPDATE jobs SET status = ?, logurl = ?, start_timestamp = ?, end_timestamp = ?, arches = ? WHERE id = ?',
                     ('succeeded' if passed else 'failed', buildurl, started, finished, arches, buildnumber))

    # XXX: opt-in list of maintainers for now
    #
    # Doing the fetch and deploy under the 'apache' user is not a good idea.
    # Instead we mark the build as ready to fetch, which a separate process
    # does.
    if (reference == 'refs/heads/master') and (package != 'playground') and (maintainer in ['Jon Turney']):
        if passed:
            with sqlite3.connect(carpetbag.dbfile) as conn:
                conn.execute("UPDATE jobs SET status = 'fetching', artifacts = ? WHERE id = ?", (' '.join(artifacts.values()), buildnumber))

    return '200 OK', ''


if __name__ == '__main__':
    with sqlite3.connect(carpetbag.dbfile) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id integer primary key, srcpkg text, hash text, user text, status text, logurl text, start_timestamp integer, end_timestamp integer, arches text, artifacts text, ref text)''')

    cgitb.enable(logdir=basedir, format='text')
    try:
        status, content = hook()
        print('Status: %s' % status)
        print()
        print(content)
    except BaseException:
        # allow cgitb to do it's thing
        print('Content-Type: text/plain')
        print('Status: 422')
        print()
        raise
