#!/usr/bin/env python3
#
# process to fetch and deploy build artifacts from appveyor
#

import daemon
import logging
import logging.handlers
import os
import pathlib
import pidlockfile
import shutil
import signal
import socket
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


_LOGGER = logging.getLogger(__name__)
logging.getLogger('inotify.adapters').propagate = False


def fetch():
    incomplete = False
    trigger = False

    with sqlite3.connect(carpetbag.dbfile) as conn:
        c = conn.execute("SELECT id, user, arches, artifacts, backend FROM jobs WHERE status = 'fetching'")

        rows = c.fetchall()

        if len(rows) > 0:
            _LOGGER.info('%d rows ready for fetching' % len(rows))

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

                    _LOGGER.info('fetching %s to %s' % (url, tmpfile.name))

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

                _LOGGER.info('unpacking to %s' % dest)
                r = subprocess.run(['unzip', '-o', tmpfile.name, '-d', dest],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)

                for l in r.stdout.decode('utf-8').splitlines():
                    _LOGGER.info('unzip: %s' % l)

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
                _LOGGER.info('moving to %s' % staging)
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
            _LOGGER.info('%d rows ready for fetching metadata' % len(rows))

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
                _LOGGER.info("fetching metadata for %s failed, will retry later" % buildid)
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
        _LOGGER.error(e)
        incomplete = True

    return incomplete


def logging_setup():
    # setup logging to a file
    rfh = logging.handlers.TimedRotatingFileHandler('/sourceware/cygwin-staging/logs/scallywag-fetch.log', backupCount=48, when='midnight')
    rfh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s'))
    rfh.setLevel(logging.DEBUG)
    _LOGGER.addHandler(rfh)

    # setup logging to stdout
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(os.path.basename(sys.argv[0]) + ': %(message)s'))
    ch.setLevel(logging.INFO)
    _LOGGER.addHandler(ch)

    # turn off filtering on level in root logger (which defaults to WARNING, and
    # which all non-root loggers delegate to by default)
    logging.getLogger().setLevel(logging.NOTSET)


def main():
    context = daemon.DaemonContext(stdout=sys.stdout,
                                   stderr=sys.stderr,
                                   umask=0o002,
                                   pidfile=pidlockfile.PIDLockFile('/sourceware/cygwin-staging/lock/scallywag-fetch.pid'))

    def sigterm(signum, frame):
        _LOGGER.debug("SIGTERM")
        context.terminate(signum, frame)

    context.signal_map = {
        signal.SIGTERM: sigterm,
        signal.SIGHUP: None,
    }

    with context:
        logging_setup()
        _LOGGER.info("scallywag-fetch daemon started, pid %d" % (os.getpid()))
        _LOGGER.info('has_inotify %s' % has_inotify)

        try:
            incomplete = True
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
            _LOGGER.error("exception %s" % (type(e).__name__), exc_info=True)

    _LOGGER.info("scallywag-fetch daemon stopped")


if __name__ == '__main__':
    main()
