#!/usr/bin/env python3
#
# start or cancel a package build via backend API
#

import logging
import logging.handlers
import os
import sqlite3
import time

import backends
import carpetbag


# subclass TimedRotatingFileHandler with open umask
class SharedTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    def _open(self):
        old_umask = os.umask(0o000)
        rtv = logging.handlers.RotatingFileHandler._open(self)
        os.umask(old_umask)
        return rtv


rfh = SharedTimedRotatingFileHandler('/sourceware/cygwin-staging/logs/build-request.log', backupCount=48, when='midnight')
rfh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s'))
rfh.setLevel(logging.DEBUG)

logging.getLogger().addHandler(rfh)
logging.getLogger().setLevel(logging.NOTSET)


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
    now = time.time()
    with sqlite3.connect(carpetbag.dbfile) as conn:
        cursor = conn.execute('INSERT INTO jobs (srcpkg, hash, ref, user, status, timestamp, tokens) VALUES (?, ?, ?, ?, ?, ?, ?)',
                              (package, commit, reference, maintainer, 'requested', now, tokens))
        buildnumber = cursor.lastrowid
        conn.commit()
    conn.close()

    # select backend
    if 'appveyor' in default_tokens:
        backend_name = 'appveyor'
    else:
        backend_name = 'github'

    # request job
    backend = backends.lookup_by_name(backend_name)
    if backend:
        bbid, buildurl = backend.request_build(package, maintainer, commit, reference, default_tokens, buildnumber)

    # an error occurred requesting the job
    if bbid < 0:
        print('scallywag: error queuing build {0} on {1}'.format(buildnumber, backend_name))
        return

    print('scallywag: build {0} queued on {1}'.format(buildnumber, backend_name))
    print('scallywag: https://cygwin.com/cgi-bin2/jobs.cgi?id={0}'.format(buildnumber))

    # record job as pending
    with sqlite3.connect(carpetbag.dbfile) as conn:
        conn.execute('UPDATE jobs SET status = ?, logurl = ?, backend = ?, backend_id = ? WHERE id = ?',
                     ('pending', buildurl, backend_name, bbid, buildnumber))
        conn.commit()
    conn.close()


def cancel_build(backend, bbid):
    backend = backends.lookup_by_name(backend)
    if backend:
        backend.cancel_build(bbid)
