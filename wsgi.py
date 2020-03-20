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

#
# wsgi database queries
#

import sqlite3
import textwrap
import wsgiref.simple_server

dbfn = '/home/jon/scallywag/carpetbag.db'
conn = sqlite3.connect(dbfn)


def serve_job_results():
    header = 'Jobs'
    result = textwrap.dedent('''\
                             <!DOCTYPE html>
                             <html>
                             <head>
                             <title>%s</title>
                             </head>
                             <body>
                             <h1>%s</h1>
                             <table>''' % (header, header))

    result += '<tr><th>id</th><th>srcpkg</th><th>status</th><th>logs</th><th>elapsed</th></tr>'

    c = conn.execute('''SELECT * FROM jobs''')
    for row in c:
        (jobid, srcpkg, commit, status, logurl, start_ts, end_ts) = row
        result += '<tr><td>%d</td><td>%s</td><td>%s</td><td><a href="%s">[log]</a></td><td>%s</td></tr>' % (jobid, srcpkg, status, logurl, end_ts - start_ts)

    result += textwrap.dedent('''\
                             </table>
                             </body>
                             </html>''')
    return result


def scallywag_app(environ, start_response):
    status = '200 OK'  # HTTP Status
    headers = [('Content-type', 'text/html; charset=utf-8')]  # HTTP Headers
    start_response(status, headers)

    result = serve_job_results()

    return [result.encode()]


if __name__ == "__main__":
    httpd = wsgiref.simple_server.make_server('', 8888, scallywag_app)
    print("Serving on port 8888...")

    # Serve until process is killed
    httpd.serve_forever()
