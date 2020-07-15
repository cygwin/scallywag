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

    # request originates from appveyor, or me
    if os.environ['REMOTE_ADDR'] not in ['138.91.141.243', '86.158.32.4']:
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
                artifacts[arch] = job['artifacts'][0]['permalink']

    arches = ' '.join(sorted(arches))
    logging.info('buildno: %d, passed %s, package: %s, commit: %s, arches: %s' % (buildnumber, passed, package, commit, arches))

    with sqlite3.connect(carpetbag.dbfile) as conn:
        cursor = conn.execute('SELECT id FROM jobs WHERE id = ?', (buildnumber,))
        if cursor.fetchone():
            conn.execute('UPDATE jobs SET srcpkg = ?, hash = ?, user = ?,  status = ?, logurl = ?, start_timestamp = ?, end_timestamp = ?, arches = ? WHERE id = ?',
                         (package, commit, maintainer, 'succeeded' if passed else 'failed', buildurl, started, finished, arches, buildnumber))
        else:
            conn.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (buildnumber, package, commit, maintainer, 'succeeded' if passed else 'failed', buildurl, started, finished, arches))
        conn.commit()

    content = ''

    # XXX: opt-in list for now
    # XXX: allowing this to run under 'apache' user is problematic
    if (reference == 'refs/heads/master') and (maintainer in []):
        if passed:
            for arch in artifacts:
                try:
                    content += subprocess.check_output([os.path.join(basedir, 'fetch-artifacts'),
                                                        "\'%s\'" % maintainer, arch, artifacts[arch]])
                except subprocess.CalledProcessError as e:
                    content += e.output

    return '200 OK', content


if __name__ == '__main__':
    with sqlite3.connect(carpetbag.dbfile) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id integer primary key, srcpkg text, hash text, user text, status text, logurl text, start_timestamp integer, end_timestamp integer, arches text)''')

    cgitb.enable(logdir='/tmp/scallywag/', format='text')
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
