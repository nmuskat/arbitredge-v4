"""
data_v4 -- external research datasets with provenance.

Raw files live OUTSIDE git (data/raw/, ignored); every download is recorded
in data/raw/MANIFEST.json (url, sha256, bytes, fetched_at, licence note), so
any run can be reproduced byte-for-byte or detected as drifted.
"""
