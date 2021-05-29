# Scallywag, or how does this daisy-chain of hacks work?

## (GitHub backend edition)

1. `gitolite`

    In `.gitolite.rc`, `LOCAL_CODE` points to `$HOME/.gitlite/local`.

    `$LOCAL_CODE/hooks/common` contains a link to the `post-receive` script,
    which is thus linked to in all repositories hooks directory.

2. `post-receive`

    `GL_REPO` is set in the environment by `gitolite`.

    `CYGNAME` is set in the environment by `.ssh/authorized_keys`.

    If the git receive updated a reference, this queues a build using the GitHub
    repository dispatch REST API, parameterized by BUILDNUMBER, PACKAGE,
    MAINTAINER, COMMIT etc.

3. `.github/workflows/scallywag.yml`

    a. Installs Cygwin.

    b. Invokes `scallywag`.

    Stores BUILDNUMBER, PACKAGE, MAINTAINER, COMMIT and ARCH etc. in a JSON
    build artifact.

    Checks out the specific COMMIT of the PACKAGE repository which triggered
    this build.

    Builds package artifacts using `cygport`.

    c. GitHub POSTs a workflow_run event to a GitHub App webhook when the
    workflow is complete.

4. `gh-hook.cgi`

    Extracts status and artifacts information from the event.

    Extracts BUILDNUMBER, PACKAGE, MAINTAINER, COMMIT from the JSON artifact.

    Stores all that information in an sqlite db. `jobs.cgi` provides a web interface
    to examine that information.

    If the git reference updated was 'master', deploy is enabled for this
    maintainer, and not disabled for this package, mark the package artifacts as
    ready to fetch.

5. `fetch.py`

    Fetch the build artifacts, unpack them into CYGNAME's upload area on sourceware,
    and request upload processing by calm.

## TODO

- job to make a src pkg, pass that as an artifact to binary pkg jobs
- authorized users should be able to cancel or retry a job, somehow
- don't be terrible
