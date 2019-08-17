#!/usr/bin/env python3

import json
import sqlite3
import time
import web

urls = ('/hook/.*', 'hooks')

app = web.application(urls, globals())


def parse_time(s):
    time_format = '%m/%d/%Y %I:%M %p'  # e.g. "8/17/2019 4:41 PM"
    st = time.strptime(s, time_format)
    t = time.mktime(st)
    return int(t)


class hooks:
    def POST(self):
        data = web.data()
        j = json.loads(data)
        # print('DATA RECEIVED:')
        # print(json.dumps(j, sort_keys=True, indent=4))

        buildnumber = j['eventData']['buildNumber']
        buildurl = j['eventData']['buildUrl']
        passed = j['eventData']['passed']
        started = parse_time(j['eventData']['started'])
        finished = parse_time(j['eventData']['finished'])

        messages = j['eventData']['jobs'][0]['messages']
        message = messages[0]['message']

        print(buildnumber)
        print(buildurl)
        print(passed)
        print(started)
        print(finished)
        print(message)

        evars = {i[0]: i[1] for i in map(lambda m: m.split(': ', 1), message.split('; '))}
        package = evars['PACKAGE']
        commit = evars['COMMIT']
        print(package)
        print(commit)

        with sqlite3.connect('carpetbag.db') as conn:
            conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (buildnumber, package, commit, 'succeeded' if passed else 'failed', buildurl, started, finished))
            conn.commit()

        return 'OK'


if __name__ == '__main__':
    with sqlite3.connect('carpetbag.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id integer primary key, srcpkg text, hash text, status text, logurl text, start_timestamp integer, end_timestamp integer)''')

    app.run()
