"""Stage 3.32B Autonomy Control Plane.

The mandatory authorization layer for every autonomous operation in Stage 3.32 and later:

* ``registry``      - the closed catalogue of action types (permitted and permanently prohibited).
* ``control_plane`` - autonomy levels, kill switches, approval queue, execution, append-only audit.
* ``handlers``      - the only code an approved action can run (all local, none touches production).
* ``engine``        - the observe / recommend assessment built on Stage 3.32A Operational Intelligence.

Nothing in this package can start, stop or change production, publish, or modify configuration.
"""
