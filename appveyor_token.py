#!/usr/bin/env python3
#
# obtain credentials for an appveyor build
#

import os


def fetch_token():
    account = 'cygwin'

    basedir = os.path.dirname(os.path.realpath(__file__))
    secretfile = os.path.join(basedir, 'appveyor.token')
    with open(secretfile, 'r') as f:
        token = f.read().strip()

    return (account, token)


if __name__ == '__main__':
    print(fetch_token())
