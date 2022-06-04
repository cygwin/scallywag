#!/usr/bin/env python3

import logging
import os
import sqlite3

basedir = os.path.dirname(os.path.realpath(__file__))
dbfile = os.path.join(basedir, 'carpetbag.db')


# object to hold the data for an update
class Update:
    def __init__(self):
        pass


def deploy(maintainer, tokens):
    if ('nobuild' in tokens) or ('nodeploy' in tokens):
        return False

    if 'deploy' in tokens:
        return True

    return False


def update(u):
    # sort, because it's important that 'arch' and 'artifacts' are in the same order!
    u.arch_list = ' '.join(sorted(u.artifacts.keys()))
    logging.info(vars(u))

    with sqlite3.connect(dbfile) as conn:
        cursor = conn.execute('SELECT id FROM jobs WHERE id = ?', (u.buildnumber,))
        if not cursor.fetchone():
            conn.execute('INSERT INTO jobs (id, srcpkg, hash, ref, user) VALUES (?, ?, ?, ?, ?)',
                         (u.buildnumber, u.package, u.commit, u.reference, u.maintainer))

        conn.execute('UPDATE jobs SET status = ?, logurl = ?, duration = ?, arches = ? WHERE id = ?',
                     (u.status, u.buildurl, u.duration, u.arch_list, u.buildnumber))

        if u.status != 'succeeded':
            return

        # Doing the fetch and deploy under the 'apache' user is not a good idea.
        # Instead we mark the build as ready to fetch, which a separate process
        # does.
        if (u.reference == 'refs/heads/master') and (u.package != 'playground') and deploy(u.maintainer, u.tokens):
            conn.execute("UPDATE jobs SET status = 'fetching', artifacts = ? WHERE id = ?", (' '.join([u.artifacts[a] for a in sorted(u.artifacts.keys())]), u.buildnumber))

        if 'nobuild' in u.tokens:
            conn.execute("UPDATE jobs SET status = 'not built' WHERE id = ?", (u.buildnumber,))
