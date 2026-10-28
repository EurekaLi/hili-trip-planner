# 性能门禁

`read_api.js` 只请求 liveness 和公开隐私说明，不触发 LLM、MCP 或图片提供方。
默认以每秒 10 次到达率运行 30 秒，要求失败率低于 1%、检查通过率高于 99%、
整体 P95 小于 750 ms，并输出 `reports/k6-read-summary.json`。

```powershell
$env:BASE_URL="http://127.0.0.1:8080"
$env:REQUEST_RATE="10"
$env:DURATION="30s"
k6 run tests/phase6/performance/read_api.js
```

旅行生成负载会产生真实费用，不进入默认 CI。只有配置专用沙箱 Key、任务费用预算
和清理策略后，才能在 `live_contract/` 增加并显式运行生成容量测试。
