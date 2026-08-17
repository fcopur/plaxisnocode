# p3d/ — reference Plaxis command logs (the ground truth)

This folder holds sample Plaxis 3D projects and their **command logs**:
recordings of every scripting command a hand-built model sent to Plaxis
Input (saved as `.p3d` files from a real Plaxis session).

They are the project's ground truth. When you build a model by hand in
Plaxis and drop its files here, the AI assistant reads the exact commands
Plaxis used — verbatim property names (`E50Ref`, `gammaUnsat`, …), call
order, geometry values — and makes the `.params` files reproduce them.
The mock-build check (`python3 tools/mock_build.py`) compares the commands
the builder would send against these logs.

Everything in this folder except this README is **git-ignored**: the files
are large, machine-generated, and specific to your own study. Drop your own
samples here locally whenever you want to give the assistant fresh ground
truth; mention them in `instructions.txt` or `feedbacks.txt`.
