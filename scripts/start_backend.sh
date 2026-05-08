#!/bin/bash

/bin/bash scripts/compile_po.sh
exec python -m saver_backend
