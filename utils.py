#!/usr/bin/env python3
#
# utility functions
#

import getpass
import os
import pwd


def get_maintainer():
    if 'CYGNAME' in os.environ:
        # if we're invoked by ssh-wrapper, CYGNAME will be set
        maintainer = os.environ['CYGNAME']
    else:
        # otherwise, try to turn UID in a full name
        gecos = pwd.getpwuid(os.getuid()).pw_gecos
        maintainer = gecos.split(',', 1)[0]

        # failing that, use login name
        if not maintainer:
            maintainer = getpass.getuser()

    return maintainer
