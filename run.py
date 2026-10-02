#!/usr/bin/env python3
"""StickStory Studio — local YouTube production dashboard.

Run:  python run.py        then open http://localhost:8000
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
