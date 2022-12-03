#!/usr/bin/env python3
#
# Copyright (c) 2016 Jon Turney
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import logging
import os
import re
import subprocess
import sys


class PackageKind:
    def __init__(self, kind=None, script='', depends=None, arches=None, tokens=None):
        if depends is None:
            depends = set()
        if arches is None:
            arches = []
        if tokens is None:
            tokens = []

        self.kind = kind
        self.script = script
        self.depends = depends
        self.arches = arches
        self.tokens = tokens


var_list = [
    'ARCHES',
    'BUILD_REQUIRES',
    'CROSS_HOST',
    'DEPEND',
    'INHERITED',
    'SCALLYWAG',
]
var_values = {}


def get_var(var, default=None):
    if var not in var_list:
        logging.error('unanticipated variable %s' % var)

    if var not in var_values:
        if default is None:
            logging.error('variable %s not set but has no default' % var)
        return default

    return var_values.get(var)


#
# analyze the source repository
#

def analyze(repodir, default_tokens):
    files = os.listdir(repodir)
    cygports = [m for m in files if re.search(r'\.cygport$', m)]

    # more than one cygport!
    if len(cygports) > 1:
        logging.error('repository contains multiple .cygport files')
        return PackageKind()

    # exactly one cygport file
    if len(cygports) == 1:
        fn = cygports[0]
        logging.info('repository contains cygport %s' % fn)

        # there's an ordering problem with some cygclasses, which always check
        # for their prerequisites when included, irrespective of the cygport
        # sub-command being used, so 'vars' will fail when we use it to
        # determine what the prerequisites are...
        #
        # we should fix this somehow in cygport, but for the moment, we can work
        # around this by setting the __cygport_check_prog_req_nonfatal env var.
        env = os.environ.copy()
        env['__cygport_check_prog_req_nonfatal'] = '1'

        # some cygports take this as a signal to not raise an error when the
        # environment is not as expected
        env['cygport_no_error'] = '1'

        # extract interesting variables from cygport
        try:
            result = subprocess.run(['cygport', fn, 'vars'] + var_list,
                                    check=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    env=env)
        except subprocess.CalledProcessError as e:
            logging.error('cygport vars failed, exit status %d' % e.returncode)
            logging.error(e.stderr)
            return PackageKind()

        output = result.stdout.decode()

        # elide any information messages
        output = re.sub(r'^\x1b.*\*\*\* Info:.*\n', r'', output, flags=re.MULTILINE)

        for m in re.finditer(r'^(?:declare -[-r] |)(.*?)="(.*?)"$', output, re.MULTILINE | re.DOTALL):
            # TBD: handle shell escapes in value?
            var_values[m.group(1)] = m.group(2)
            logging.info('%s="%s"' % (m.group(1), m.group(2)))

        # workaround for a bug cygport
        # (arch probing gets information messages from nested invocation into ARCH)
        if '***' in get_var('ARCHES'):
            var_values['ARCHES'] = 'all'

        # does it have a BUILD_REQUIRES or DEPEND line?
        depends = get_var('BUILD_REQUIRES', '') + ' ' + get_var('DEPEND', '')
        depends = depends_from_depend(depends)
        logging.info('build dependencies (from BUILD_REQUIRES): %s' % (','.join(sorted(depends))))

        # extract any SCALLYWAG line
        tokens = default_tokens
        scallywag = get_var('SCALLYWAG', '')
        if scallywag:
            tokens.extend(scallywag.split())
            logging.info('cygport SCALLYWAG: %s' % tokens)

        # detect if there is an ARCH line
        arches = get_var('ARCHES')
        if arches == 'all':
            arches = 'x86_64'
        arches = arches.split()

        # some 'inherit's imply ARCH=noarch
        inherited = get_var('INHERITED').split()
        if any(i in inherited for i in ['cross', 'texlive']):
            arches = ['noarch']

        depends = set.union(depends, depends_from_inherits(inherited))

        return PackageKind(kind='cygport', script=fn, depends=depends, arches=arches, tokens=tokens)

    # if there's no cygport file, we look for a g-b-s style .sh file instead
    scripts = [m for m in files if re.search(r'\.sh$', m)]
    if len(scripts) == 1:
        fn = scripts[0]
        f = open(os.path.join(repodir, fn), 'rb')
        # analyze it's content to classify as cygbuild or g-b-s
        # (some copies of cygbuild contain a latin1 encoded 'í' i-acute)
        content = f.read().decode(errors='replace')
        if re.search('^CYGBUILD', content, re.MULTILINE):
            kind = 'cygbuild'
        else:
            kind = 'g-b-s'

        logging.info('repository contains a %s-style build script %s' % (kind, fn))
        return PackageKind(kind=kind, script=fn)
    elif len(scripts) > 1:
        logging.error('too many scripts in repository')
        return PackageKind()

    logging.error("couldn't find build instructions in repository")
    return PackageKind()


#
# inheriting certain classes implies some build depends
#

# the mapping from cross-host target triples to package prefixes
cross_package_prefixes = {
    'i686-w64-mingw32': 'mingw64-i686-',
    'x86_64-w64-mingw32': 'mingw64-x86_64-',
    'i686-pc-cygwin': 'cygwin32-',
    'x86_64-pc-cygwin': 'cygwin64-',
}


def depends_from_inherits(inherits):
    build_deps = set()

    logging.info('cygport inherits: %s' % ','.join(sorted(inherits)))

    # if we have any of the inherits in the first list, add the second list to
    # depends
    for (pos, deps) in [
            (['cmake', 'kde4', 'kf5', 'qt4-cmake'], ['cmake', 'ninja', 'make']),
            (['gnome2'], ['gnome-common']),
            (['kf5'], ['extra-cmake-modules']),
            (['lua'], ['lua', 'liblua-devel']),
            (['mate'], ['mate-common']),
            (['meson'], ['meson', 'pkg-config']),
            (['ninja'], ['ninja']),
            (['ocaml'], ['ocaml', 'flexdll']),
            (['perl'], ['perl']),
            (['php'], ['php-devel', 'php-PEAR']),
            (['python2', 'python'], ['python2']),
            (['python2-distutils'], ['python2-setuptools', 'python2-devel']),
            (['python2-wheel', 'python-wheel'], ['python2-wheel', 'python2-pip']),
            (['python3'], ['python3']),
            (['python3-distutils'], ['python3-setuptools', 'python3-devel']),
            (['python3-wheel', 'python-wheel'], ['python3-devel', 'python36-wheel', 'python36-pip', 'python37-wheel', 'python37-pip', 'python38-wheel', 'python38-pip', 'python39-wheel', 'python39-pip']),  # done correctly, this needs to understand PYTHON_WHEEL_VERSIONS
            (['qt5'], ['libQt5Core-devel', 'libQt5Gui-devel']),
            (['ruby'], ['ruby-devel']),
            (['tcl'], ['tcl-devel', 'tcl-tk-devel']),
            (['texlive'], ['texlive-collection-basic']),  # to ensure correct run-time dependency generation
            (['xfce4'], ['xfce4-dev-tools']),
            (['xorg'], ['xorg-util-macros']),
    ]:
        for i in pos:
            if i in inherits:
                build_deps.update(deps)

    # if it uses autotools, it will want pkg-config
    if ('autotools' in inherits) or (len(inherits) == 0):
        build_deps.add('pkg-config')

    # for cross-packages, we need the appropriate cross-toolchain
    if 'cross' in inherits:
        cross_host = get_var('CROSS_HOST')
        pkg_prefix = cross_package_prefixes.get(cross_host, '')
        logging.info('cross_host: %s, pkg_prefix: %s' % (cross_host, pkg_prefix))

        for tool in ['binutils', 'gcc-core', 'gcc-g++', 'pkg-config']:
            build_deps.add('%s%s' % (pkg_prefix, tool))

    logging.info('build dependencies (deduced from inherits): %s' % (','.join(sorted(build_deps))))

    return build_deps


#
# transform a cygport BUILD_REQUIRES (or deprecated DEPEND) list into a list of
# cygwin packages
#
# (BUILD_REQUIRES can only contain cygwin package names, and hence doesn't
# require the complexity of handling DEPEND dependency atoms)
#

def depends_from_depend(depend):
    build_deps = set()

    for atom in depend.split():
        # atoms of the form blah(foo) are the obsolete DEPEND syntax for
        # indicating a module foo of type blah.  warn about them for now, to
        # become an error in the future.
        match = re.match(r'(.*)\((.*)\)', atom)
        if match:
            logging.warning('unhandled DEPEND atom %s' % (atom))
        # otherwise, it is simply a cygwin package name
        else:
            build_deps.add(atom)

    return build_deps


#
# analyse the specified directory
#

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO)
    logging.basicConfig(format=os.path.basename(sys.argv[0]) + ': %(message)s')
    print(analyze(sys.argv[1], []).__dict__)
