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
from urllib.parse import urlencode

import carpetbag

dbfn = carpetbag.dbfile
rows_per_page = 25
conn = sqlite3.connect('file:%s?mode=ro' % dbfn, uri=True)
conn.row_factory = sqlite3.Row

def results(parse):
    page = int(parse.get('page', 1))
    highlight = int(parse.get('id', 0))

    result = textwrap.dedent('''\
                             <!DOCTYPE html>
                             <html lang="en">
                             <head>
                             <meta http-equiv="refresh" content="300">
                             <link rel="stylesheet" type="text/css" href="/style.css" />
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
                                 <th>when</th>
                                 <th>duration</th></tr>''')

    def options_list(column):
        selected = parse.get(column, '')
        sql = 'SELECT DISTINCT %s FROM jobs ORDER BY %s' % (column, column)
        c = conn.execute(sql)
        opts = [''] + [r[0] for r in c]
        return ('<select name="%s" form="filter">' % (column)
                + ''.join(['<option%s>%s</option>' % (' selected' if o == selected else '', o) for o in opts])
                + '</select>')

    result += textwrap.dedent('''<tr><td><form id="filter" method="get"><button>Filter</button></form></td>
                                 <td>%s</td>
                                 <td>%s</td>
                                 <td>%s</td>
                                 <td></td>
                                 <td></td>
                                 <td></td>
                                 <td></td>
                                 <td></td>
                                 <td></td></tr>''' % (options_list('srcpkg'),
                                                      options_list('status'),
                                                      options_list('user')))

    where_list = []
    where_params = ()
    for w in ['user', 'status', 'srcpkg']:
        if w in parse:
            where_list.append("%s = ?" % (w))
            where_params = where_params + (parse[w],)
    where_clause = ''
    if where_list:
        where_clause = 'WHERE ' + 'AND '.join(where_list)

    sql = 'SELECT COUNT(*) FROM jobs %s' % (where_clause)
    c = conn.execute(sql, where_params)
    (rows,) = c.fetchone()
    maxpages = int((rows + (rows_per_page - 1)) / rows_per_page)
    if page < 1:
        page = 1
    if page > maxpages:
        page = maxpages

    sql = 'SELECT * FROM jobs %s' % where_clause + 'ORDER BY id DESC LIMIT ?,?'
    c = conn.execute(sql, where_params + ((page - 1) * rows_per_page, rows_per_page))
    for row in c:
        jobid = row['id']
        srcpkg = row['srcpkg']
        commit = row['hash']
        username = row['user']
        status = row['status']
        logurl = row['logurl']
        timestamp = row['timestamp']
        duration = row['duration']
        arches = row['arches']
        artifacts = row['artifacts']
        ref = row['ref']

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
            result += '<td>%s</td>' % (arches.replace('source ',''))
        else:
            result += '<td></td>'

        if timestamp:
            result += '<td>%s</td>' % (datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S'))
        else:
            result += '<td></td>'

        if duration:
            result += '<td>%s</td>' % (int(duration))
        else:
            result += '<td></td>'

        result += '</tr>'

    result += '</table>'

    def query_string_modify_page(page):
        parse['page'] = page
        return urlencode(parse)

    result += '<div class="gridfooter">'
    result += '<div class="floatleft">'
    result += '<a href="https://cygwin.com/git/?p=cygwin-apps/scallywag.git">scallywag</a>'
    result += '</div>'
    result += '<div class="center">'
    if page > 1:
        result += '<a href="?%s">previous</a>' % query_string_modify_page(page - 1)
    result += ' page %d of %d ' % (page, maxpages)
    if page < (maxpages - 1):
        result += '<a href="?%s">next</a>' % query_string_modify_page(page + 1)
    result += '</div>'
    result += '</div>'

    result += textwrap.dedent('''</body>
                                 </html>''')
    return result


if __name__ == "__main__":
    cgitb.enable()
    print('Content-Type: text/html')
    print()

    parse = cgi.parse()

    # if any query variable appears more than once, use the value of the last
    # occurence.
    parse = {k:v[-1] for k, v in parse.items()}

    print(results(parse))
