# Crew CI

代码改动继续在 Windows 上执行完整 Python、网页与桌面测试，以及隔离交互验收。纯 README、`docs/` 下 Markdown 或独立 `docs/prototypes/` 原型改动跳过此工作流；Skill 提示词、SQL、接口夹具和工作流本身的改动仍会触发。

后端保持一台 Windows runner、一个标准 unittest 进程，不新增测试调度器、第三方依赖或额外 runner。标准发现自动纳入新增用例和子包，避免多进程建库放大托管 Windows 的磁盘竞争。

通用功能夹具从真实初始化生成一次空库模板，每个用例复制到独立的临时 home，再调用真实存储入口。这样减少托管 Windows 上重复建表的磁盘写入；不共享用例数据，不关闭同步落盘，已有迁移与存储测试仍走真实路径。

```powershell
cd plugin
python -m unittest discover -s tests --durations 10
```

需要逐条查看用例时仍可加 `-v`，日常 CI 使用简洁输出和最慢十项的耗时。

失败与导入错误保留完整证据并返回非零状态，结束时汇总用例和跳过数量。执行前清除外层 Crew/Claude 身份，各测试继续使用隔离 home 和假载体。

协议替身必须回复收到的 RPC，包括会话标题同步；不支持的方法明确返回协议错误。真实载体的超时与授权规则保持原样。桌面依赖使用 setup-node 的 npm 下载缓存，安装仍按 lockfile 执行 `npm ci`。
