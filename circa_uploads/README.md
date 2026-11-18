# Circa splits sheet uploads

Upload @BettingSplits' Circa splits image here (GitHub → this folder → Add file → Upload files → Commit).
The `circa` workflow reads every new image, matches every sport's games and DraftKings lines on ESPN, applies rule `circa99.v2`
(`splits_v4/circa_engine.py`), and the site's **Circa 99%** tab updates a few minutes later. One image per upload; the
upload time is the commit time (a sheet uploaded after kickoff is marked LIVE).
