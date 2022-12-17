#!/usr/bin/env python3
#
# process to fetch and deploy build artifacts from appveyor
#

import daemon
import lockfile.pidlockfile
import logging
import logging.handlers
import os
import pathlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import inotify.adapters
    has_inotify = True
except ImportError:
    has_inotify = False

import carpetbag
import gh
import gh_token


def fetch():
    with sqlite3.connect(carpetbag.dbfile) as conn:
        scan = False
        c = conn.execute("SELECT id, user, arches, artifacts, backend FROM jobs WHERE status = 'fetching'")
        if c.rowcount > 0:
            logging.info('fetched %d rows' % c.rowcount)
        for r in c:
            buildid = r[0]
            user = r[1]
            backend = r[4]
            for arch, art in zip(r[2].split(), r[3].split()):
                if arch == 'source':
                    continue

                with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
                    # fetch artifact to a tempfile
                    if art.startswith('http'):
                        url = art
                    else:
                        url = 'https://ci.appveyor.com/api/buildjobs/%s/artifacts/artifacts.zip' % (art)

                    req = urllib.request.Request(url)

                    if backend == 'github':
                        req.add_header('Authorization', 'Bearer ' + gh_token.fetch_iat())

                    logging.info('fetching %s' % url)
                    with urllib.request.urlopen(req) as response:
                        shutil.copyfileobj(response, tmpfile)
                # close tmpfile

                # unpack to staging area
                dest = '/sourceware/cygwin-staging/staging/%s/%s/release' % (user, arch)
                os.makedirs(dest, exist_ok=True)
                logging.info('unpacking to %s' % dest)
                r = subprocess.run(['unzip', '-o', tmpfile.name, '-d', dest],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)

                for l in r.stdout.decode('utf-8').splitlines():
                    logging.info('unzip: %s' % l)

                # mark as ready for calm
                if r.returncode == 0:
                    pathlib.Path(dest, '!ready').touch()
                    scan = True

                # remove tmpfile
                os.remove(tmpfile.name)

                # update status to deployed
                conn.execute("UPDATE jobs SET status = 'deployed' WHERE id = ?", (buildid,))

        # signal calm to scan staging
        if scan:
            try:
                pid = int(open('/sourceware/cygwin-staging/calm.pid').read())
                try:
                    logging.info('signalled calm to scan staging area')
                    os.kill(pid, signal.SIGUSR1)
                except ProcessLookupError:
                    pass
            except FileNotFoundError:
                pass

    conn.close()


def fetch_metadata():
    incomplete = False

    with sqlite3.connect(carpetbag.dbfile) as conn:
        c = conn.execute("SELECT id, backend, backend_id FROM jobs WHERE status = 'fetching metadata'")
        rows = c.fetchall()

        if len(rows) > 0:
            logging.info('fetched metadata %d rows' % c.rowcount)

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
    incomplete = fetch_metadata()
    fetch()
    return incomplete


def logging_setup():
    # setup logging to a file
    rfh = logging.handlers.TimedRotatingFileHandler(os.path.join('/sourceware/cygwin-staging/logs/scallywag-fetch.log'), backupCount=48, when='midnight')
    rfh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s'))
    rfh.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(rfh)

    # setup logging to stdout
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(os.path.basename(sys.argv[0]) + ': %(message)s'))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(ch)

    # no filtering on level in root logger
    logging.getLogger().setLevel(logging.NOTSET)


def main():
    context = daemon.DaemonContext(stdout=sys.stdout,
                                   stderr=sys.stderr,
                                   umask=0o002,
                                   pidfile=lockfile.pidlockfile.PIDLockFile('/sourceware/cygwin-staging/scallywag-fetch.pid'))

    def sigterm(signum, frame):
        logging.debug("SIGTERM")
        context.terminate(signum, frame)

    context.signal_map = {
        signal.SIGTERM: sigterm,
    }

    with context:
        logging_setup()
        logging.info("scallywag-fetch daemon started, pid %d" % (os.getpid()))
        logging.info('has_inotify %s' % has_inotify)

        try:
            incomplete = False
            # wake when db is changed, or periodically if we have incompletely
            # processed changes
            while True:
                if has_inotify and not incomplete:
                    i = inotify.adapters.Inotify()

                    i.add_watch(carpetbag.dbfile)
                    for event in i.event_gen(yield_nones=False):
                        (_, type_names, path, filename) = event
                        if 'IN_CLOSE_WRITE' in type_names:
                            # remove watch so we don't see events generated by
                            # our own changes
                            i.remove_watch(carpetbag.dbfile)
                            incomplete = process()
                            break

                else:
                    incomplete = process()
                    time.sleep(60)

        except Exception as e:
            logging.error("exception %s" % (type(e).__name__), exc_info=True)

    logging.info("scallywag-fetch daemon stopped")


if __name__ == '__main__':
    main()
