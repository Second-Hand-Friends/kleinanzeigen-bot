# Demo ad fixtures

This directory contains sanitized demo ads copied from ignored `data/demo_ads/` and selected `data/my_ads/` entries.
They are tracked so `pdm run verify-dom-assumptions` can use them as local demo inputs for the maintainer-only diagnostic.

Rules:
- keep these fixtures tracked in git
- do not add real user ads here
- keep YAML/image references consistent
- contact data is synthetic and varied on purpose
- ids, hashes, timestamps, and repost counts are synthetic
- keep the fixtures loadable by `scripts/verify_dom_assumptions.py`
- do not treat them as product fixtures; they exist for this diagnostic only
