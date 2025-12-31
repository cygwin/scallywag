#!/usr/bin/env python3
#
# slightly generic interface to backend APIs
#

import logging

import appveyor
import gh


# Backend class is expected to implement the following static methods:
#
#     def request_build(package, maintainer, commit, reference, default_tokens, buildnumber):
#     def cancel_build(bbid):
#     def update_build_status(bbid):


def lookup_by_name(backend):
    if backend == 'appveyor':

        return appveyor.Backend
    elif backend == 'github':
        return gh.Backend
    else:
        logging.warning('unknown backend: %s' % backend)
        return None
