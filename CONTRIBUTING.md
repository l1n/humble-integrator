# Contributing

The most useful contribution is **verifying a recipe**: run the tool against
your own Humble purchase, see whether the redemption steps, installer handling
and launch target are right, fix what isn't, and set `verified = true`.

- Recipe format and matching rules: `recipes/README.md`.
- How recipes are researched (sources, what to cite, what not to guess):
  `recipes/AGENT_BRIEF.md` — written for the AI agents that drafted most of the
  book, and just as true for people.
- Before sending a change:

  ```sh
  for t in tests/test_*.py; do python3 -I "$t"; done
  python3 -I humble_integrator/recipes.py check
  python3 -I humble_integrator/recipes.py cover --census data/census.jsonl --conflicts
  ```

  `check` must report 0 problems and `--conflicts` should stay at 0.

Ground rules: cite a source for every non-obvious claim; never record a guess
as fact (use `notes` and `launch.find` globs instead); and nothing in this
project bypasses a vendor's licence check, reveals or redeems a key without
the user asking, or redistributes anyone's software.
