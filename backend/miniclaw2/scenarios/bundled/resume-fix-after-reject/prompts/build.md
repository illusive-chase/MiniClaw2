Create a small Python module at `mathutils.py` in the project root that
exports a single function `add(a, b)` returning `a + b`. Do not add any
other functions — the reviewer will ask for the rest in a follow-up
turn if they want it.

Then write your review brief to `.miniclaw2/outputs/<this node id>/brief.md`
with these three sections (markdown):

# How to run
The reviewer should run `python3 -c "import mathutils; print(mathutils.add(2,3))"` and confirm it prints `5`.

# What to verify
- The file `mathutils.py` exists at the project root.
- It exports `add(a, b)` and only `add(a, b)`.
- Decide whether the module should also expose `subtract(a, b)`.

# Review note
Submit a free-form review asking for `subtract(a, b)` to be added in the
follow-up turn.
