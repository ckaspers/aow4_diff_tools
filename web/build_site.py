#!/usr/bin/env python3
"""Put the browser version together in _site/ (what GitHub Pages serves): the page and its worker
from web/, plus the files the worker loads (its APP list). Nothing else from the repo is served.

    python web/build_site.py
    python -m http.server -d _site       then open http://localhost:8000
"""
import os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def main():
    out = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 1 else '_site')
    worker = open(os.path.join(HERE, 'worker.js'), encoding='utf-8').read()
    app = re.findall(r"'([^']+)'", re.search(r'const APP = \[(.*?)\];', worker, re.S).group(1))
    if os.path.isdir(out): shutil.rmtree(out)
    os.makedirs(out)
    for f in ('index.html', 'worker.js'):
        shutil.copy2(os.path.join(HERE, f), out)
    for f in app:
        shutil.copy2(os.path.join(ROOT, f), out)
    print('%d files -> %s' % (2 + len(app), out))

if __name__ == '__main__':
    main()
