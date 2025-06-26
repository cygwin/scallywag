#!/bin/sh

ARGS="$@"

if [ $(uname -o) == 'Cygwin' ]
then
  # installed packages may have added files to /etc/profile.d/, so re-read profile
  source /etc/profile
  # restore cwd after /etc/profile sets it to $HOME
  cd - >/dev/null
fi

# run required cygport command
cygport ${ARGS} || exit 1
