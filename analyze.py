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
    def __init__(self, kind=None, script='', depends=None, arches=None, tokens=None, announce=''):
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
        self.announce = announce


var_list = [
    'ARCHES',
    'BUILD_REQUIRES',
    'CROSS_HOST',
    'DEPEND',
    'INHERITED',
    'RESTRICT',
    'SCALLYWAG',
    'ANNOUNCE',
]
var_values = {}


def cygport_vars(fn):
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
                                capture_output=True,
                                env=env)
    except subprocess.CalledProcessError as e:
        logging.error('cygport vars failed, exit status %d' % e.returncode)
        logging.error(e.stderr.decode())
        logging.error(e.stdout.decode())
        return False

    output = result.stdout.decode()

    # elide any information messages
    output = re.sub(r'^\x1b.*\*\*\* Info:.*\n', r'', output, flags=re.MULTILINE)

    for m in re.finditer(r'^(?:declare -[-r] |)(.*?)=(?:"|\$\')(.*?)(?:"|\')$', output, re.MULTILINE | re.DOTALL):
        name = m.group(1)

        value = m.group(2)
        # handle shell escapes in a $'' value
        if name == 'ANNOUNCE':
            value = value.replace(r'\n', '\n')
        else:
            value = value.replace(r'\n', ' ')
        value = value.replace(r'\t', ' ')

        var_values[name] = value
        logging.info('%s="%s"' % (m.group(1), value))

    # workaround for a bug cygport
    # (arch probing gets information messages from nested invocation into ARCH)
    if '***' in get_var('ARCHES'):
        var_values['ARCHES'] = 'all'

    return True


def get_var(var, default=None):
    if var not in var_list:
        logging.error('unanticipated variable %s' % var)

    if var not in var_values:
        if default is None:
            logging.error('variable %s not set but has no default' % var)
        return default

    return var_values.get(var)


def parse_cygport(fn):
    logging.info('parsing cygport %s' % fn)

    with open(fn) as f:
        content = f.read()

        # discard comments
        content = re.sub(r'#.*$', '', content, flags=re.MULTILINE)

        # fold any line-continuations
        content = re.sub(r'\\\n', '', content)

        # Does it have a line that sets or adds to the value of a variable of
        # interest? (Note that this only approximates the value.  The only
        # accurate way to evaluate it is to execute the cygport).
        for var in var_list + ['ARCH']:
            value = ''
            matches = re.finditer(r'^\s*' + var + r'(?:\+|)=\s*("?)(.*?)\1\s*$', content, re.MULTILINE | re.DOTALL)
            for match in matches:
                if value:
                    value += ' '
                value += match.group(2)
            if value:
                var_values[var] = value

        # Work out what ARCHES should have been
        if 'ARCH' in var_values:
            var_values['ARCHES'] = var_values.pop('ARCH')
        else:
            var_values['ARCHES'] = 'all'

        # Also look for inherits lines, to work out what INHERITED should have
        # been
        inherits = ''
        for l in content.splitlines():
            match = re.match('^inherit(.*)', l)
            if match:
                inherits += match.group(1) + ' '
        var_values['INHERITED'] = inherits

        for var in var_values:
            logging.info('%s="%s"' % (var, var_values[var]))


#
# analyze the source
#

def analyze(repodir, default_tokens):
    files = os.listdir(repodir)
    cygports = [m for m in files if re.search(r'\.cygport$', m)]

    # more than one cygport!
    if len(cygports) > 1:
        logging.error('source contains multiple .cygport files')
        return PackageKind()

    # exactly one cygport file
    if len(cygports) == 1:
        fn = cygports[0]
        logging.info('source contains cygport %s' % fn)

        if not cygport_vars(fn):
            # fallback to trying to parse the cygport (as previously)
            parse_cygport(os.path.join(repodir, fn))

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

        if 'upload' in get_var('RESTRICT', ''):
            tokens.add('nodeploy')
            logging.info("cygport RESTRICT contains 'upload', adding 'nodeploy'")

        # detect if there is an ARCH line
        arches = get_var('ARCHES')
        if arches == 'all':
            arches = 'x86_64'
        arches = arches.split()

        # some 'inherit's imply ARCH=noarch
        inherited = get_var('INHERITED').split()
        if any(i in inherited for i in ['cross', 'texlive']):
            arches = ['noarch']

        # for cross-packages, we need the appropriate cross-toolchain
        if 'cross' in inherited:
            cross_host = get_var('CROSS_HOST')
            pkg_prefix = cross_package_prefixes.get(cross_host, '')
            if not pkg_prefix:
                logging.error('cross_host: %s, pkg_prefix is unknown' % (cross_host))
                return PackageKind()
            logging.info('cross_host: %s, pkg_prefix: %s' % (cross_host, pkg_prefix))

            for tool in ['binutils', 'gcc-core', 'gcc-g++', 'pkg-config']:
                depends.add('%s%s' % (pkg_prefix, tool))

        depends.update(depends_from_inherits(inherited))

        announce = get_var('ANNOUNCE', '')

        return PackageKind(kind='cygport', script=fn, depends=depends, arches=arches, tokens=tokens, announce=announce)

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

        logging.info('source contains a %s-style build script %s' % (kind, fn))
        return PackageKind(kind=kind, script=fn)
    elif len(scripts) > 1:
        logging.error('too many scripts in source')
        return PackageKind()

    logging.error("couldn't find build instructions in source")
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
            (['python2-wheel'], ['python2-wheel', 'python2-pip']),
            (['python3'], ['python3']),
            (['python3-distutils'], ['python3-setuptools', 'python3-devel']),
            (['python3-wheel', 'python-wheel'],
             ['python3-devel',
              'python36-devel', 'python36-wheel', 'python36-pip',
              'python37-devel', 'python37-wheel', 'python37-pip',
              'python38-devel', 'python38-wheel', 'python38-pip',
              'python39-devel', 'python39-wheel', 'python39-pip']),  # done correctly, this needs to understand PYTHON_WHEEL_VERSIONS
            (['qt5'], ['libQt5Core-devel', 'libQt5Gui-devel']),
            (['ruby'], ['ruby-devel']),
            (['tcl'], ['tcl-devel', 'tcl-tk-devel']),
            (['texlive'], ['texlive-collection-basic']),  # to ensure correct run-time dependency generation
            (['wxwidgets'], ['libwx_baseu3.0-devel', 'libwx_gtk3u3.0-devel']),  # done correctly, this needs to understand WX_VERSION
            (['xfce4'], ['xfce4-dev-tools']),
            (['xorg'], ['xorg-util-macros']),
            (['xvfb'], ['xorg-server', 'xf86-video-dummy', 'xinit', 'xorg-server-extra']),
    ]:
        for i in pos:
            if i in inherits:
                build_deps.update(deps)

    # if it uses autotools, it will want pkg-config
    if ('autotools' in inherits) or (len(inherits) == 0):
        build_deps.add('pkg-config')

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
