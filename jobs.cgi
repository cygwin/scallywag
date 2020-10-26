#!/usr/bin/env python3
#
# Copyright (c) 2019 Jon Turney
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

import cgi
import cgitb
import datetime
import sqlite3
import textwrap

import carpetbag

dbfn = carpetbag.dbfile
rows_per_page = 25
conn = sqlite3.connect(dbfn)


def results(page, highlight):
    result = textwrap.dedent('''\
                             <!DOCTYPE html>
                             <html lang="en">
                             <head>
                             <meta http-equiv="refresh" content="300">
                             <link rel="stylesheet" type="text/css" href="/static/builds/style.css" />
                             <title>Cygwin package builds</title>
                             </head>
                             <body>
                             <table class="grid">''')

    result += textwrap.dedent('''<tr><th>id</th>
                                 <th>source package</th>
                                 <th>status</th>
                                 <th>by</th>
                                 <th>commit</th>
                                 <th>ref</th>
                                 <th>logs</th>
                                 <th>arch</th>
                                 <th>start</th>
                                 <th>elapsed</th></tr>''')

    c = conn.execute('SELECT COUNT(*) FROM jobs')
    (rows,) = c.fetchone()
    maxpages = (rows + (rows_per_page - 1)) / rows_per_page
    if page < 1:
        page = 1

    c = conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT %d,%d' % ((page - 1) * rows_per_page, rows_per_page))
    for row in c:
        (jobid, srcpkg, commit, username, status, logurl, start_ts, end_ts, arches, artifacts, ref) = row
        commiturl = 'https://cygwin.com/git-cygwin-packages/?p=git/cygwin-packages/%s.git;a=commitdiff;h=%s' % (srcpkg, commit)
        shorthash = commit[0:8]

        if jobid == highlight:
            result += '<tr class="highlight">'
        else:
            result += '<tr>'

        if srcpkg != 'playground':
            srcpkglink = '<a href="https://cygwin.com/packages/summary/%s-src.html">%s</a>' % (srcpkg, srcpkg)
        else:
            srcpkglink = '%s' % (srcpkg)

        result += textwrap.dedent('''<td>%d</td>
                                     <td>%s</td>
                                     <td class="%s">%s</td>
                                     <td>%s</td>
                                     <td><a href="%s">%s</a></td>''') % (jobid, srcpkglink, status, status, username, commiturl, shorthash)

        if ref:
            ref = ref.replace('refs/heads/', '')
            ref = ref.replace('refs/tags/', '')
            result += '<td>%s</td>' % (ref)
        else:
            result += '<td></td>'

        if logurl:
            result += '<td><a href="%s">[log]</a></td>' % (logurl)
        else:
            result += '<td></td>'

        if arches:
            result += '<td>%s</td>' % (arches)
        else:
            result += '<td></td>'

        if end_ts and start_ts:
            elapsed = end_ts - start_ts
            start = datetime.datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S')
            result += textwrap.dedent('''<td>%s</td>
                                         <td>%s</td>''') % (start, elapsed)
        else:
            result += textwrap.dedent('''<td></td>
                                         <td></td>''')

        result += '</tr>'

    result += '</table>'

    result += '<div class="pagination">'
    if page > 1:
        result += '<a href="?page=%d">previous</a>' % (page - 1)
    result += ' page %d of %d ' % (page, maxpages)
    if page < (maxpages - 1):
        result += '<a href="?page=%d">next</a>' % (page + 1)
    result += '</div>'

    result += textwrap.dedent('''</body>
                                 </html>''')
    return result


if __name__ == "__main__":
    cgitb.enable()
    print('Content-Type: text/html')
    print()

    parse = cgi.parse()
    page = int(parse.get('page', [1])[0])
    row = int(parse.get('id', [0])[0])

    print(results(page, row))
