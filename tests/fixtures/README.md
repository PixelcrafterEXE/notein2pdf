# Test fixtures (local only, not in git)

Each sub-directory holds one real note exported from the Notein app twice:

    <name>/note.in    native export  (Export | Share -> Native)
    <name>/app.pdf    the app's own PDF export of the same note (Export | Share -> Standard)

`tests/test_fixtures.py` converts `note.in` and compares the result against `app.pdf`.
These files contain personal notes and copyrighted material, so everything here except this
README and `.gitkeep` is git-ignored. Tests that need fixtures are skipped when they are absent;
`tests/test_synthetic.py` needs none.
