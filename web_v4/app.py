"""
web_v4/app.py -- read-only ArbitrEdge V4 website.

  uvicorn web_v4.app:app --host 127.0.0.1 --port 8400

Serves data/site/snapshot.json only. There is no write endpoint, and the
app opens no database or provider connection.
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from web_v4 import render

SNAPSHOT = os.environ.get("ARBITREDGE_V4_SNAPSHOT", "data/site/snapshot.json")
app = FastAPI(title="ArbitrEdge V4", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(render.HERE, "static")), name="static")
_env = render.env()


@app.get("/", response_class=HTMLResponse)
def index():
    return render.render_page(_env, "index", render.load_snapshot(SNAPSHOT))


@app.get("/{page}", response_class=HTMLResponse)
def page(page: str):
    if page not in dict(render.PAGES):
        raise HTTPException(404)
    return render.render_page(_env, page, render.load_snapshot(SNAPSHOT))
