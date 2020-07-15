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


def fetch():
    with sqlite3.connect(carpetbag.dbfile) as conn:
        scan = False
        c = conn.execute("SELECT id, user, arches, artifacts FROM jobs WHERE status = 'fetching'")
        if c.rowcount > 0:
            logging.info('woken to fetch %d rows' % c.rowcount)
        for r in c:
            buildid = r[0]
            user = r[1]
            for arch, jobid in zip(r[2].split(), r[3].split()):
                with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
                    # fetch artifact to a tempfile
                    url = 'https://ci.appveyor.com/api/buildjobs/%s/artifacts/artifacts.zip' % (jobid)
                    logging.info('fetching %s' % url)
                    with urllib.request.urlopen(url) as response:
                        shutil.copyfileobj(response, tmpfile)
                # close tmpfile

                # unpack to upload area
                dest = '/sourceware/cygwin-staging/home/%s/%s/release' % (user, arch)
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

        # signal calm to scan uploads
        if scan:
            try:
                pid = int(open('/sourceware/cygwin-staging/calm.pid').read())
                try:
                    logging.info('signalled calm to scan upload area')
                    os.kill(pid, signal.SIGUSR1)
                except ProcessLookupError:
                    pass
            except FileNotFoundError:
                pass


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

        try:
            if has_inotify:
                # wake when db is changed
                i = inotify.adapters.Inotify()
                i.add_watch(carpetbag.dbfile)
                for _event in i.event_gen(yield_nones=False):
                    fetch()
            else:
                while True:
                    fetch()
                    time.sleep(60)
        except Exception as e:
            logging.error("exception %s" % (type(e).__name__), exc_info=True)

    logging.info("scallywag-fetch daemon stopped")


if __name__ == '__main__':
    main()
