"""
tools/mock_build.py — verify a build WITHOUT Plaxis.

Plaxis (and ``plxscripting``) is not available in the assistant's environment,
so this script fakes the scripting server and records every command the
builder would send. Compare the printed verbs/counts against the reference
command logs in ``p3d/`` to confirm a change does what the logs do.

Usage (from the project root)::

    python3 tools/mock_build.py                       # build defaults.params
    python3 tools/mock_build.py tasks/sand_Dr15.params  # a task (all combos)
    python3 tools/mock_build.py --verbs my.params     # full verb sequence

How the fake server works: ``Mock`` is a recursive stand-in — any attribute
returns another Mock, any call is recorded and returns a Mock, and iterating
``g_i.Soils`` (or ``g_i.Surfaces``/``Polygons``) yields objects with composite
names (``Soil_1_Soil_2_Soil_3_1``…) so the re-finds by creation-time name
token — ``set_soil_block_material`` and ``refine_block_surfaces`` — are
exercised the same way Plaxis' decomposed volumes exercise them.
"""

import sys
import types
from collections import Counter

CALLS = []          # (dotted path, args) for every recorded g_i/s_i call

# names Plaxis gives the slab's soil pieces after the overlapping fill block
# is extruded (see CLAUDE.md "Model semantics"); the slab's captured token
# "Soil_2" must match the first two and not the third.
_MESHED_SOIL_NAMES = ("Soil_1_Soil_2_Soil_3_1", "Soil_1_Soil_2_Soil_3_2",
                      "Soil_1_Soil_3_1")

# names Plaxis gives the decomposed z=0 surface pieces (coplanar cap, plate
# and load intersect the fill block's polygon); the captured token "Polygon_4"
# must match the first two and not the third.
_MESHED_SURFACE_NAMES = ("Polygon_4_1",
                         "Polygon_1_Polygon_2_Polygon_3_Polygon_4_1",
                         "Polygon_1_1")


class Mock:
    """Recursive mock: any attribute/call returns another Mock; iterable."""

    def __init__(self, path):
        self._path = path

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return Mock(f"{self._path}.{name}")

    def __call__(self, *args, **kwargs):
        CALLS.append((self._path, args))
        return Mock(f"{self._path}()")

    def __iter__(self):
        if self._path.endswith("Soils"):
            yield from map(_named, _MESHED_SOIL_NAMES)
        elif self._path.endswith(("Surfaces", "Polygons")):
            yield from map(_named, _MESHED_SURFACE_NAMES)
        else:
            yield Mock(f"{self._path}[0]")

    def __getitem__(self, i):
        return Mock(f"{self._path}[{i}]")

    def __len__(self):
        return 1                    # collections look non-empty -> activated

    def __str__(self):
        # _object_name() ends in str(); make creation-time captures yield the
        # tokens the composite names above carry, so the re-finds match.
        if ".Soil" in self._path and ".Name" in self._path:
            return "Soil_2"                 # the slab's captured soil name
        if ".surface(" in self._path and ".Name" in self._path:
            return "Polygon_4"              # a block's captured polygon name
        return self._path


def _named(name):
    """A Mock posing as a decomposed piece: _object_name() returns ``name``."""
    obj = Mock(f"<{name}>")
    obj.__dict__["value"] = obj                  # _plx_value -> itself
    obj.__dict__["Name"] = types.SimpleNamespace(value=name)
    return obj


def _install_fake_plxscripting():
    def fake_new_server(host, port, password=""):
        return Mock("s_i"), Mock("g_i")

    easy = types.ModuleType("plxscripting.easy")
    easy.new_server = fake_new_server
    pkg = types.ModuleType("plxscripting")
    pkg.easy = easy
    sys.modules["plxscripting"] = pkg
    sys.modules["plxscripting.easy"] = easy
    return fake_new_server


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    show_verbs = "--verbs" in args
    if show_verbs:
        args.remove("--verbs")
    path = args[0] if args else None

    fake_new_server = _install_fake_plxscripting()
    import plaxis3d.model
    plaxis3d.model.new_server = fake_new_server   # in case it imported first
    from plaxis3d import build_from_params

    build_from_params(path)

    verbs = [p.split(".")[-1] for p, _ in CALLS]
    print()
    print(f"TOTAL recorded calls: {len(verbs)}")
    if show_verbs:
        for p, call_args in CALLS:
            print(f"  {p}  {call_args if call_args else ''}")
    else:
        for verb, count in Counter(verbs).most_common():
            print(f"{count:5d}  {verb}")


if __name__ == "__main__":
    sys.path.insert(0, ".")         # run from the project root
    main()
