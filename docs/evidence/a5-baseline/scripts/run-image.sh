#!/usr/bin/env bash
# 只重采集「图片状态」：启动 Vite 前端 → headless Edge 渲染 ResultsPage → 关闭 Vite
set -u
cd /d/Desktop/新建文件夹 || exit 1
export VITE_API_PROXY_TARGET=http://127.0.0.1:8001

nohup node ./node_modules/vite/bin/vite.js --config main/vite.config.ts --host 127.0.0.1 --port 4173 --strictPort > .tmp/a5/vite6.log 2>&1 &
VPID=$!
READY=0
for i in $(seq 1 40); do
  sleep 2
  CODE=$(env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy curl -s -o /dev/null -m 2 -w "%{http_code}" http://127.0.0.1:4173/ 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "vite ready after ${i}x2s"; break; fi
done
if [ "$READY" != "1" ]; then
  echo "vite failed"; tail -20 .tmp/a5/vite6.log; kill $VPID 2>/dev/null; exit 3
fi

node .tmp/a5/capture6.mjs
RC=$?
echo "capture6 exit=$RC"
kill $VPID 2>/dev/null
exit $RC
