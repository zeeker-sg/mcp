# Synthetic private identity CLI demonstration

Run from the repository root with Python 3.11 or later.

```sh
PYTHONPATH=src python3 tests/identity/fixtures/private_cli_demo.py
```

The script uses the standard library and runs offline. It creates two invented
metadata-only SQLite sources, their pinned contracts and one private registry in
a fresh system temporary directory outside the checkout. It removes those
artifacts on exit. The eLitigation-shaped URL strings exercise the judgment
normalizer. The script makes no network requests.

The judgments fixture has five rows, including two exact copies with the same
legacy ID and raw citation. A third row uses a differently padded citation and
URL. The three rows generate three neutral-citation pairs and three URL-variant
pairs. The two other judgments share a party and proceeding with those rows and
each receives only an individual review proposal.

The PDPC fixture has three rows, including two exact copies with the same legacy
ID and decision URL. It deliberately reuses a judgments URL, legacy ID and
logical source ID. PDPC produces three individual review proposals in its own
corpus and kind.

The script verifies the exact selected metadata multiset per snapshot, the
expected duplicate groups, idempotent import and candidate generation, and claim
membership. It also verifies that all eight assertions remain unbound at revision
zero and the registry contains zero documents. It prints a JSON summary on
success and exits nonzero on a failed expectation.

The approval reference in the script applies only to these invented fixtures.
