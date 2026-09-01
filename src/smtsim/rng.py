"""Named, independent random streams derived from one master seed.

The simulation draws every random number from a stream identified by name --
``arrivals``, ``station:pick_and_place:service``, and so on. Two runs started
from the same master seed therefore see the same numbers on every stream they
share, whatever else differs between them.

That property is what makes a what-if comparison honest. If a single RNG fed
the whole model, giving the placer a second head would shift every subsequent
draw, and the measured difference in throughput would mix the change under test
with a reshuffled sample. With one stream per station, the printer sees exactly
the same numbers in both runs, and the comparison is paired.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

ARRIVALS_STREAM = "arrivals"


def derive_seed(master_seed: int, stream_name: str) -> int:
    """Map a master seed and a stream name to a stream seed.

    BLAKE2b rather than the built-in ``hash``: ``hash`` of a string is salted
    per process by PYTHONHASHSEED, which would make runs reproducible only
    within a single interpreter.
    """
    payload = f"{master_seed}:{stream_name}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


@dataclass(frozen=True, slots=True)
class RngStreams:
    """A factory for the independent streams belonging to one run."""

    master_seed: int

    def stream(self, name: str) -> random.Random:
        """A fresh generator for the named stream."""
        return random.Random(derive_seed(self.master_seed, name))

    def arrivals(self) -> random.Random:
        return self.stream(ARRIVALS_STREAM)

    def station_service(self, station_name: str) -> random.Random:
        return self.stream(f"station:{station_name}:service")

    def station_failures(self, station_name: str) -> random.Random:
        """Kept separate from the service stream on purpose.

        If a station's failure draws and service draws shared one generator,
        the interleaving between them would depend on the run's timing, so
        turning failures on for a station would also reshuffle its service
        times. Separate streams keep each effect isolated.
        """
        return self.stream(f"station:{station_name}:failures")
