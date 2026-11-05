# TrueClock Lab

This repository runs reproducible chip timing experiments on a standard public Linux runner. Each admitted build binds its RTL, constraints, clock period, SKY130 HD libraries, and implementation recipe to immutable SHA-256 digests. The dispatcher assigns a unique attempt and an expiring execution window.

The **Physical build** workflow runs `scripts/run_flow.py` for one committed request from `jobs/`. Its result package records generated timing reports, standard-cell metrics, the final mapped netlist, and per-file digests. The package also records the digests of omitted layout databases. Functional proof and physical acceptance require separate evidence.

The **Farm health** workflow records the dispatched Git tree, runner resources, and hashes of the health-check files. Each workflow run returns a bounded evidence artifact and its SHA-256 digest.
