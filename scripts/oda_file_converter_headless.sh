#!/usr/bin/env bash
# ODA File Converter 仅提供 Qt xcb 后端；在无桌面的服务器中按需创建虚拟显示。
set -euo pipefail

export QT_QPA_PLATFORM=xcb
exec xvfb-run -a -s "-screen 0 1024x768x24" /usr/bin/ODAFileConverter "$@"
