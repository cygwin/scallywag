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


def deployable_token(tokens):
    if ('nobuild' in tokens) or ('nodeploy' in tokens):
        return False

    if 'deploy' in tokens:
        return True

    return False


def deployable_job(u):
    return ((u.status == 'succeeded') and
            (u.reference == 'refs/heads/master') and
            (u.package != 'playground'))


def update_status(u):
    logging.info(vars(u))

    with sqlite3.connect(dbfile) as conn:
        # if the id isn't available, determine it from backend_id
        if not hasattr(u, 'buildnumber'):
            cursor = conn.execute('SELECT id FROM jobs WHERE backend_id = ?', (u.backend_id,))
            u.buildnumber = cursor.fetchone()[0]

        conn.execute('UPDATE jobs SET status = ?, logurl = ?, duration = ? WHERE id = ?',
                     (u.status, u.buildurl, u.duration, u.buildnumber))

        if u.status != 'succeeded':
            return

        # The only piece of new data the metadata actually provides is the
        # updated token set, after adding tokens from the cygport itself
        if not hasattr(u, 'tokens'):
            conn.execute("UPDATE jobs SET status = 'fetching metadata' WHERE id = ?", (u.buildnumber,))

    conn.close()


def update_metadata(u):
    logging.info(vars(u))

    with sqlite3.connect(dbfile) as conn:
        if 'nobuild' in u.tokens:
            conn.execute("UPDATE jobs SET status = 'not built' WHERE id = ?", (u.buildnumber,))
            return

        # sort, because it's important that 'arch' and 'artifacts' are in the same order!
        u.arch_list = ' '.join(sorted(u.artifacts.keys()))
        conn.execute("UPDATE jobs SET arches = ?, artifacts = ? WHERE id = ?", (u.arch_list, ' '.join([u.artifacts[a] for a in sorted(u.artifacts.keys())]), u.buildnumber))

        if not hasattr(u, 'status'):
            u.status = 'succeeded'

        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (u.status, u.buildnumber))

    conn.close()
    deploy(u)


# Doing the fetch and deploy under the 'apache' user is not a good idea.
# Instead we mark the build as ready to fetch, which a separate process does.
def deploy(u, force=False):
    if deployable_job(u) and (deployable_token(u.tokens) or force):
        with sqlite3.connect(dbfile) as conn:
            conn.execute("UPDATE jobs SET status = 'fetching' WHERE id = ?", (u.buildnumber,))
        conn.close()
        return True

    return False
