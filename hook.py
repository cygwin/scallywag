#!/usr/bin/env python3

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import web

urls = ('/hook/.*', 'hooks')

app = web.application(urls, globals())


def parse_time(s):
    time_format = '%m/%d/%Y %I:%M %p'  # e.g. "8/17/2019 4:41 PM"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


class hooks:
    def POST(self):
        if web.ctx.ip != '138.91.141.243':
            web.ctx.status = '403 Forbidden'
            return 'Forbidden'

        auth = web.ctx.env.get('HTTP_AUTHORIZATION', '')
        auth = re.sub('^Basic ', '', auth)
        if auth != os.environ.get('SCALLYWAG_AUTH'):
            web.ctx.status = '401 Unauthorized'
            return 'Unauthorized'

        data = web.data()
        j = json.loads(data)
        with open('last.json', 'w') as f:
            print(json.dumps(j, sort_keys=True, indent=4), file=f)

        buildnumber = j['eventData']['buildNumber']
        buildurl = j['eventData']['buildUrl']
        passed = j['eventData']['passed']
        started = parse_time(j['eventData']['started'])
        finished = parse_time(j['eventData']['finished'])
        artifacts = {}

        for job in j['eventData']['jobs']:
            messages = job['messages']
            message = messages[0]['message']

            evars = {i[0]: i[1] for i in map(lambda m: m.split(': ', 1), message.split('; '))}
            package = evars['PACKAGE']
            commit = evars['COMMIT']
            arch = evars['ARCH'].replace('i686', 'x86')
            maintainer = evars['MAINTAINER']

            if arch != 'skip':
                if len(job['artifacts']):
                    artifacts[arch] = job['artifacts'][0]['permalink']

        arches = ' '.join(artifacts.keys())
        logging.info('buildno: %d, passed %s, package: %s, commit: %s, arches: %s' % (buildnumber, passed, package, commit, arches))

        with sqlite3.connect('carpetbag.db') as conn:
            conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (buildnumber, package, commit, maintainer, 'succeeded' if passed else 'failed', buildurl, started, finished, arches))
            conn.commit()

        # XXX: opt-in list for now
        if maintainer not in ['Jon Turney']:
            return

        if passed:
            for arch in artifacts:
                subprocess.call(['ssh', 'cygwin-admin@cygwin.com', '/sourceware/cygwin-staging/scallywag/fetch-artifacts',
                                 "\'%s\'" % maintainer, arch, artifacts[arch]])

        return 'OK'


if __name__ == '__main__':
    if 'SCALLYWAG_AUTH' not in os.environ:
        print('SCALLYWAG_AUTH env var not set')
        sys.exit(1)

    with sqlite3.connect('carpetbag.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id integer primary key, srcpkg text, hash text, user text, status text, logurl text, start_timestamp integer, end_timestamp integer, arches text)''')

    app.run()
