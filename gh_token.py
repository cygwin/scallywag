#!/usr/bin/env python3

# locate imports on sourceware
import sys
sys.path.insert(0, '/home/cygwin/.local/lib/python{}.{}/site-packages'.format(sys.version_info.major, sys.version_info.minor))

from cryptography.hazmat.backends import default_backend
import json
import jwt
import os
import time
import urllib.error
import urllib.request

GH_APP_ID = 117451

private_key = None


def _get_private_key():
    global private_key
    if not private_key:
        # load the GitHub app private key
        basedir = os.path.dirname(os.path.realpath(__file__))
        pemfile = os.path.join(basedir, 'scallywag.private-key.pem')
        cert = open(pemfile, 'r').read().encode()
        private_key = default_backend().load_pem_private_key(cert, None)

    return private_key


def _make_jwt():
    now = int(time.time())

    payload = {
        # issued at time, 60 seconds in the past to allow for clock drift
        'iat': now - 60,
        # expiration time (10 minute maximum)
        'exp': now + (10 * 60),
        # GitHub App's identifier
        'iss': GH_APP_ID,
    }

    return jwt.encode(payload, _get_private_key(), algorithm='RS256')


def fetch_iat():
    token = _make_jwt()

    # list installations for this app
    req = urllib.request.Request('https://api.github.com/app/installations')
    req.add_header('Authorization', 'Bearer {}'.format(token))
    req.add_header('Accept', 'application/vnd.github.v3+json')
    resp = urllib.request.urlopen(req)

    # find the installation_id for the installation on the 'cygwin' org
    j = json.loads(resp.read().decode())
    for i in j:
        if i['account']['login'] == 'cygwin':
            access_tokens_url = i['access_tokens_url']
            break
    else:
        return None

    # create an installation access token
    req = urllib.request.Request(access_tokens_url, method='POST')
    req.add_header('Authorization', 'Bearer {}'.format(token))
    req.add_header('Accept', 'application/vnd.github.v3+json')
    resp = urllib.request.urlopen(req)

    j = json.loads(resp.read().decode())
    return j['token']


def fetch_auth():
    if 'GITHUB_DEBUG_OWNER' in os.environ:
        owner = os.environ['GITHUB_DEBUG_OWNER']

        basedir = os.path.dirname(os.path.realpath(__file__))
        secretfile = os.path.join(basedir, 'github.token')
        with open(secretfile, 'r') as f:
            token = f.read().strip()
    else:
        owner = 'cygwin'
        token = fetch_iat()

    return (owner, token)


if __name__ == '__main__':
    print(fetch_auth())
