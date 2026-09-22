#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/opt/translation_system
release=/home/ubuntu/releases/docx-font-fix-20260922
test ! -e "$release"
test "$(sha256sum app/services/document_exporter.py | cut -d ' ' -f 1)" = 3d5b9514ada7f14f395450f367f97fff6b07e42364cdf5749ed6f0eb02d8ac40
mkdir -p "$release"
cp app/services/document_exporter.py "$release/document_exporter.before.py"
sudo -n docker inspect ai-translation-app --format '{{.Image}}' > "$release/baseline-image.txt"
sudo -n docker tag "$(cat "$release/baseline-image.txt")" ai-translation-system:font-fix-baseline-20260922
cp /tmp/docx-font-fix-qa/document_exporter.py /tmp/docx-font-fix-qa/Dockerfile "$release/"
cp /tmp/docx-font-fix-qa/test_docx_export_fonts.py "$release/"
sudo -n docker build -t ai-translation-system:font-fix-20260922 "$release"
sudo -n docker run --rm --network none --entrypoint python ai-translation-system:font-fix-20260922 -c 'import app.services.document_exporter as e; assert not hasattr(e, "EXPORT_FONT_FAMILY"); print("Hotfix image import OK")'
