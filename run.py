"""
Build Plaxis 3D model(s) from a task file — the one command you run.

Run it from the project folder, with Plaxis 3D Input open and its scripting
server on. The values in ``defaults.params`` are always loaded first; a task
file only lists the few things it changes. A task with a ``[loop]`` builds one
model per combination of its values.

Examples
--------
    python run.py                          build defaults.params as-is
    python run.py tasks/sand_Dr15.params   run one task
    python run.py tasks                     run every task in the folder
    python run.py tasks/*                   run every task (shell wildcard)
    python run.py --dry-run tasks           preview all tasks, build nothing
    python run.py examples/params/01_change_values.params   a small override

Add ``--dry-run`` to any of the above to check the files without connecting to
Plaxis (it lists the models that would be built and flags problems).
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plaxis3d import build_from_params


def _collect(args):
    """Turn the command-line arguments into a list of .params files to run.

    * a directory  -> every ``*.params`` inside it (sorted)
    * a .params    -> that file
    * anything else that exists (e.g. a README) -> skipped with a note
    Returns ``[None]`` when nothing was given (build defaults.params).
    """
    if not args:
        return [None]
    files = []
    for arg in args:
        if os.path.isdir(arg):
            found = sorted(glob.glob(os.path.join(arg, "*.params")))
            if not found:
                print(f"note: no .params files in folder '{arg}' — skipped")
            files.extend(found)
        elif arg.endswith(".params") and os.path.isfile(arg):
            files.append(arg)
        elif os.path.exists(arg):
            print(f"note: '{arg}' is not a .params file — skipped")
        else:
            print(f"error: file not found: {arg}")
            sys.exit(2)
    if not files:
        print("error: no .params files to run.")
        sys.exit(2)
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Build Plaxis 3D model(s) from task .params file(s), each "
                    "merged on top of defaults.params.",
        epilog="Examples:\n" + __doc__.split("Examples\n--------")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "params", nargs="*",
        help="task .params file(s), or a folder of them. Omit to build "
             "defaults.params unchanged.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="check the file(s) and print the build plan WITHOUT connecting "
             "to Plaxis or building anything.")
    args = parser.parse_args(argv)

    files = _collect(args.params)

    all_ok = True
    model = None                       # opened once, reused for every file
    for path in files:
        result = build_from_params(path, model=model, dry_run=args.dry_run)
        if args.dry_run:
            all_ok = all_ok and (result is not False)
        else:
            model = result             # keep the same Plaxis connection

    if args.dry_run and not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
