#!/bin/sh

# SCRIPT names the build script to invoke
SCRIPT="$1"
shift
SUBCOMMANDS="$@"

# installed packages may have added files to /etc/profile.d/, so re-read profile
source /etc/profile
# restore cwd after /etc/profile sets it to $HOME
cd - >/dev/null

# run required cygport subcommands:
#
# 'download'; then 'srcpackage' (if we're making a source package); otherwise
# 'all-test' (prep/compile/install/package-test), and then 'test' (unless notest
# token is present)
for s in ${SUBCOMMANDS}
do
    cygport ${SCRIPT} ${s} || exit 1
done
