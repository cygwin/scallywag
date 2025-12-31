#!/usr/bin/env python3
#
# fetch and deploy build artifacts
#

import logging
import logging.handlers
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import urllib.request

import carpetbag
import gh
import gh_token


def fetch():
    incomplete = False
    trigger = False

    with sqlite3.connect(carpetbag.dbfile) as conn:
        c = conn.execute("SELECT id, user, arches, artifacts, backend FROM jobs WHERE status = 'fetching'")

        rows = c.fetchall()

        if len(rows) > 0:
            logging.info('%d rows ready for fetching' % len(rows))

        for r in rows:
            buildid = r[0]
            user = r[1]
            backend = r[4]
            for arch, art in zip(r[2].split(), r[3].split()):
                with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
                    if arch == 'source':
                        arch = 'src'
                    # fetch artifact to a tempfile
                    if art.startswith('http'):
                        url = art
                    else:
                        url = 'https://ci.appveyor.com/api/buildjobs/%s/artifacts/artifacts.zip' % (art)

                    req = urllib.request.Request(url)

                    if backend == 'github':
                        req.add_unredirected_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

                    logging.info('fetching %s to %s' % (url, tmpfile.name))

                    try:
                        with urllib.request.urlopen(req, timeout=60) as response:
                            shutil.copyfileobj(response, tmpfile)
                    except (socket.timeout, urllib.error.URLError) as e:
                        logging.info("archive download response %s" % e)
                        incomplete = True
                        break

                # context exit implicitly closes tmpfile

                # unpack to temporary directory
                tmpdir = '/sourceware/cygwin-staging/staging/tmp/'
                os.makedirs(tmpdir, exist_ok=True)
                dest = tempfile.mkdtemp(dir=tmpdir)

                logging.info('unpacking to %s' % dest)
                r = subprocess.run(['unzip', '-o', tmpfile.name, '-d', dest],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)

                for l in r.stdout.decode('utf-8').splitlines():
                    logging.info('unzip: %s' % l)

                # mark as ready for calm
                if r.returncode == 0:
                    pathlib.Path(dest, '!ready').touch()
                    trigger = True

                # move to staging area
                #
                # (Making all the files appear atomically ensures that the
                # !ready marker file appears synchronously with the directory.
                #
                # That greatly simplifies watching for changes on the staging
                # directory - otherwise we would need to allow for the delay in
                # establishing watches on the subdirectories to notice the
                # marker file being created)
                staging = '/sourceware/cygwin-staging/staging/%s/%s/%s/release' % (buildid, user, arch)
                logging.info('moving to %s' % staging)
                os.makedirs(staging, exist_ok=True)
                os.rename(dest, staging)

                # remove tmpfile
                os.remove(tmpfile.name)

                # update status to deployed
                conn.execute("UPDATE jobs SET status = 'deploying' WHERE id = ?", (buildid,))

    conn.close()

    # wake calm to process staging
    if trigger:
        pathlib.Path('/sourceware/cygwin-staging/staging/', '.touch').touch()

    return incomplete


def fetch_metadata():
    incomplete = False

    with sqlite3.connect(carpetbag.dbfile) as conn:
        c = conn.execute("SELECT id, backend, backend_id FROM jobs WHERE status = 'fetching metadata'")
        rows = c.fetchall()

        if len(rows) > 0:
            logging.info('%d rows ready for fetching metadata' % len(rows))

        for r in rows:
            buildid = r[0]
            backend = r[1]
            backend_id = r[2]

            if backend != 'github':
                continue

            u = carpetbag.Update()

            u.buildnumber = buildid
            u.backend_id = backend_id

            if gh.examine_run_artifacts(backend_id, u):
                carpetbag.update_metadata(u)
            else:
                logging.info("fetching metadata for %s failed, will retry later" % buildid)
                # if examine_run_artifacts fails, we'll try again later
                incomplete = True

                # XXX: if the metadata file doesn't appear even after a long
                # time after the wfr finished, that that suggests something went
                # wrong in internally in scallywag, before it writes it, in
                # which case we should change the status to errored

    conn.close()

    return incomplete


def process():
    try:
        incomplete = fetch_metadata()
        incomplete = fetch() or incomplete
    except sqlite3.OperationalError as e:
        logging.error(e)
        incomplete = True

    return incomplete
