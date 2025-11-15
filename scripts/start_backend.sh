#!/bin/bash

/bin/bash scripts/compile_po.sh
python scripts/create_instagram_session.py
/usr/local/bin/python -m saver_backend
