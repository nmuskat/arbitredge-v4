# Circa splits sheet uploads

Upload @BettingSplits' Circa splits image here (GitHub → this folder → Add file → Upload files → Commit).
The `circa` workflow reads every new image, applies rule `circa99.v1` (`splits_v4/circa_engine.py`) to NFL and
college football games, and the site's **Circa 99%** tab updates a few minutes later. One image per upload; the
upload time is the commit time (a sheet uploaded after kickoff is marked LIVE).
