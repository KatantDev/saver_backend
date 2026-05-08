#!/bin/bash

/bin/bash scripts/compile_po.sh
taskiq scheduler saver_backend.tkq:scheduler saver_backend.task_manager.events saver_backend.task_manager.tasks
