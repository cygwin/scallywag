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

from collections import namedtuple
import logging
import os
import re


PackageKind = namedtuple('PackageKind',
                         ['kind', 'script', 'depends', 'arches', 'restrict'])


#
# analyze the source repository
#

def analyze(repodir):
    files = os.listdir(repodir)
    cygports = [m for m in files if re.search(r'\.cygport$', m)]

    # more than one cygport!
    if len(cygports) > 1:
        logging.error('repository contains multiple .cygport files')
        return PackageKind(None, script='', depends=set(), arches=[], restrict=[])

    # exactly one cygport file
    if len(cygports) == 1:
        fn = cygports[0]
        f = open(os.path.join(repodir, fn))
        content = f.read()

        # fold any line-continuations
        content = re.sub(r'\\\n', '', content)

        # does it have a BUILD_REQUIRES or DEPEND line?

        # Note that this only approximates the value.  The only accurate way to
        # evaluate it is to execute the cygport.
        depend = ''
        matches = re.finditer(r'^\s*(?:DEPEND|BUILD_REQUIRES)(?:\+|)=\s*"(.*?)"', content, re.MULTILINE | re.DOTALL)
        for match in matches:
            depend += match.group(1) + ' '

        # extract any RESTRICT line
        restrict = []
        match = re.search(r'^\s*RESTRICT=\s*"?(.*?)"?$', content, re.MULTILINE)
        if match:
            restrict = match.group(1).split()
            logging.info('cygport restrict: %s' % restrict)

        # extract any ARCH line
        arches = ['i686', 'x86_64']
        match = re.search(r'^\s*ARCH=\s*"?(.*?)"?$', content, re.MULTILINE)
        if match:
            arches = match.group(1).split()

        # 'inherit cross' implies ARCH=noarch
        match = re.search(r'^\s*inherit\s*cross', content, re.MULTILINE)
        if match:
            arches = 'noarch'

        if depend:
            logging.info('repository contains cygport %s, with BUILD_REQUIRES' % fn)
            depends = set.union(depends_from_cygport(content),
                                depends_from_depend(depend))
        else:
            logging.info('repository contains cygport %s' % fn)
            depends = depends_from_cygport(content)

        return PackageKind('cygport', script=fn, depends=depends, arches=arches, restrict=restrict)

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
        return PackageKind(kind, script=fn, depends=set(), arches=[], restrict=[])
    elif len(scripts) > 1:
        logging.error('too many scripts in repository')
        return PackageKind(None, script='', depends=set(), arches=[], restrict=[])

    logging.error("couldn't find build instructions in repository")
    return PackageKind(None, script='', depends=set(), arches=[], restrict=[])


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


def depends_from_cygport(content):
    build_deps = set()
    inherits = set()

    for l in content.splitlines():
        match = re.match('^inherit(.*)', l)
        if match:
            inherits.update(match.group(1).split())

    logging.info('cygport inherits: %s' % ','.join(sorted(inherits)))

    # if we have any of the inherits in the first list, add the second list to
    # depends
    for (pos, deps) in [
            (['cmake', 'kde4', 'qt4-cmake'], ['cmake']),
            (['gnome2'], ['gnome-common']),
            (['kf5'], ['cmake', 'extra-cmake-modules']),
            (['mate'], ['mate-common']),
            (['meson'], ['meson']),
            (['ninja'], ['ninja']),
            (['python2', 'python'], ['python2']),
            (['python2-distutils'], ['python2-setuptools', 'python2-devel']),
            (['python2-wheel', 'python-wheel'], ['python2-wheel', 'python2-pip']),
            (['python3'], ['python3']),
            (['python3-distutils'], ['python3-setuptools', 'python3-devel']),
            (['python3-wheel', 'python-wheel'], ['python36-wheel', 'python36-pip', 'python37-wheel', 'python37-pip']),  # done correctly, this needs to understand PYTHON_WHEEL_VERSIONS
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
        cross_host = re.search(r'^CROSS_HOST\s*=\s*"?(.*?)"?\s*$', content, re.MULTILINE).group(1)
        pkg_prefix = cross_package_prefixes.get(cross_host, '')
        logging.info('cross_host: %s, pkg_prefix: %s' % (cross_host, pkg_prefix))

        for tool in ['binutils', 'gcc-core', 'gcc-g++', 'pkg-config']:
            build_deps.add('%s%s' % (pkg_prefix, tool))

    logging.info('build dependencies (deduced from inherits): %s' % (','.join(sorted(build_deps))))

    return build_deps


#
# transform a cygport DEPEND atom list into a list of cygwin packages
#
# (XXX: DEPEND is now deprecated, in preference to BUILD_REQUIRES, which can
# only contain cygwin package names, and hence doesn't require the complexity of
# handling dependency atoms, so this can be removed when we stop supporting
# DEPEND)
#

pkgconfig_map = eval(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pkgconfig-map')).read())


def depends_from_depend(depend):
    build_deps = set()

    for atom in depend.split():
        # atoms of the form blah(foo) indicate a module foo of type blah
        match = re.match(r'(.*)\((.*)\)', atom)
        if match:
            deptype = match.group(1)
            module = match.group(2)
            if deptype == 'girepository':
                # transform into a cygwin package name
                dep = deptype + '-' + module
                build_deps.add(dep)
            elif deptype == 'perl':
                # transform perl module name into a cygwin package name
                dep = deptype + '-' + module.replace('::', '-')
                build_deps.add(dep)
            elif deptype == 'pkgconfig':
                # a dependency on the package which contains module.pc
                module = module + '.pc'
                if module in pkgconfig_map:
                    dep = pkgconfig_map[module]
                    logging.debug('mapping %s -> %s' % (module, ','.join(sorted(dep))))
                    build_deps.update(dep)
                else:
                    logging.warning('could not map pkgconfig %s to a package' % (module))
                # also implies a dependency on pkg-config
                build_deps.add('pkg-config')
            elif deptype == 'python':
                # transform python2 module name into a cygwin package name
                dep = 'python2-' + module
            elif deptype == 'python3':
                # transform python3 module name into a cygwin package name
                dep = deptype + '-' + module
            else:
                logging.warning('DEPEND atom of unhandled type %s, module %s' % (deptype, module))
        # otherwise, it is simply a cygwin package name
        else:
            build_deps.add(atom)

    logging.info('build dependencies (from BUILD_REQUIRES): %s' % (','.join(sorted(build_deps))))
    return build_deps
