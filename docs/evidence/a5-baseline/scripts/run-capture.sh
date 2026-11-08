#!/usr/bin/env bash
# A5 基线采集：真实 Vite 前端 + 隔离后端(8001) + 真实 Edge 渲染
set -u
cd /d/Desktop/新建文件夹 || exit 1
export VITE_API_PROXY_TARGET=http://127.0.0.1:8001

rm -f .tmp/a5/vite.log
nohup node ./node_modules/vite/bin/vite.js --config main/vite.config.ts --strictPort > .tmp/a5/vite.log 2>&1 &
VPID=$!
echo "vite pid=$VPID"

READY=0
for i in $(seq 1 45); do
  sleep 2
  CODE=$(curl -s -o /dev/null -m 2 -w "%{http_code}" http://127.0.0.1:5173/ 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "vite ready after ${i}x2s"; break; fi
done
if [ "$READY" != "1" ]; then
  echo "vite failed to start"; tail -30 .tmp/a5/vite.log; kill $VPID 2>/dev/null; exit 3
fi

curl -s -m 5 -o /dev/null -w "proxy-health:%{http_code}\n" http://127.0.0.1:5173/api/health

node .tmp/a5/capture.mjs
RC=$?
echo "capture exit=$RC"
kill $VPID 2>/dev/null
wait $VPID 2>/dev/null
tail -8 .tmp/a5/vite.log
exit $RC
