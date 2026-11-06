"""HEP-Prov: provenance recorder and verifier for AI agents doing physics.

A run directory is initialized with ``hepprov init``. Every command the agent
runs goes through ``hepprov exec -- <cmd>``, which records the command, the
files it wrote (filesystem diff, any language) and the files Python processes
read (path and sha256 at read time). ``hepprov check`` verifies the
manifest's hash chain and attests every read against what the run itself
wrote.
"""

__version__ = "0.0.1"

from hepprov.emit import emit  # noqa: E402

__all__ = ["emit", "__version__"]
