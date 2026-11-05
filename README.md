# TrueClock Lab

This repository hosts reproducible chip timing experiments. The initial workflow checks whether a standard Ubuntu runner can execute a job, report its resource limits, and return an identifiable result. Each receipt binds the runner to a specific repository, commit, run number and attempt.

The `Farm health` workflow accepts a single dispatch on `main`. Its output is `health.json`, which records the runner OS, available disk and memory, tool versions, and a SHA-256 manifest for the checked-out files. Download the artifact from the workflow run and compare its archive digest against the Actions artifact metadata.

Physical measurement requires a reviewed job manifest, an admitted deadline and a separately qualified physical runner. Hardware results require actual timing reports and their associated correctness evidence.
