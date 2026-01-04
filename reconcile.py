#!/usr/bin/env python3
#
# periodically check the completion status of jobs, in case we missed a
# notification
#
# XXX: also move stuff on from 'requested' status?
# XXX:

import sqlite3
import logging

import backends
import carpetbag


def process():
    with sqlite3.connect(carpetbag.dbfile) as conn:
        c = conn.execute("SELECT id, backend, backend_id FROM jobs WHERE status = 'pending'")

        rows = c.fetchall()

        if len(rows) > 0:
            logging.info('%d rows ready for reconciling' % len(rows))

        for r in rows:
            backend_name = r[1]
            backend_id = r[2]

            logging.info('calling backend %s to reconcile for %d' % (backend_name, backend_id))

            backend = backends.lookup_by_name(backend_name)
            if backend:
                u = backend.check_build_status(backend_id)
                if u:
                    carpetbag.update_status(u)
