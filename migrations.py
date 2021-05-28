#!/usr/bin/env python3

import sqlite3

import carpetbag

if __name__ == '__main__':
    with sqlite3.connect(carpetbag.dbfile) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs
        (id integer primary key, srcpkg text, hash text, user text, status text, logurl text, start_timestamp integer, end_timestamp integer, arches text, artifacts text, ref text)''')

        # migrations go here
        cursor = conn.execute("SELECT * FROM jobs LIMIT 1")
        cols = [row[0] for row in cursor.description]
        if 'backend' not in cols:
            cursor.execute("ALTER TABLE jobs ADD COLUMN backend TEXT NOT NULL DEFAULT ''")

        print(cols)
