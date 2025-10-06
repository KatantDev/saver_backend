#!/bin/bash

# Инициализация переменной
reload=false

# Обработка аргументов
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-reload)
      reload=true
      echo "Опция --with-reload активирована"
      shift
      ;;
    *)
      echo "Неизвестная опция: $1"
      exit 1
      ;;
  esac
done

# Формируем команду с флагом --reload, если нужно
command="taskiq worker saver_backend.tkq:broker saver_backend.task_manager.events saver_backend.task_manager.tasks"
if [ "$reload" = true ]; then
  command="$command --reload"
fi

# Выполнение скрипта compile_po.sh
/bin/bash scripts/compile_po.sh

# Выполнение команды
eval $command
