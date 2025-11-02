#!/usr/bin/env bash
set -euo pipefail

echo "🔧 Fixing plural forms and escaped newlines in .po files..."

# Исправляем plural-формулы и двойные обратные слэши в \n
sed -i \
  -e 's/===/==/g' \
  -e 's/!==/!=/g' \
  -e 's/\\\\n/\\n/g' \
  locales/*/LC_MESSAGES/messages.po

echo "✅ .po files cleaned. Running make locales..."
make locales
