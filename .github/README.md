# Crew CI

代码改动继续在 Windows 上执行完整 Python、网页与桌面测试，以及隔离交互验收。纯 README、`docs/` 下 Markdown 或独立 `docs/prototypes/` 原型改动跳过此工作流；Skill 提示词、SQL、接口夹具和工作流本身的改动仍会触发。

后端在一台 runner 内使用四个独立 Python 进程并发执行测试模块，不新增第三方测试依赖或额外 runner。模块按文件大小优先调度，新增 `test*.py` 和可导入子包会自动纳入；每个模块内部仍由标准 unittest 执行。

```powershell
python .github/scripts/test_backend.py --workers 4
```

将 `--workers` 设为 `1` 可以顺序排查。原来的 `python -m unittest discover -s tests -v` 入口仍然可用，在 `plugin/` 中执行。

成功模块输出一行数量与耗时，失败模块保留完整错误和用例输出，最后汇总数量及最慢五个模块。测试失败、导入错误和未发现任何模块都会返回非零状态。子进程清除外层 Crew/Claude 身份，各测试继续使用隔离 home 和假载体。

协议替身必须回复收到的 RPC，包括会话标题同步；不支持的方法明确返回协议错误。真实载体的超时与授权规则保持原样。桌面依赖使用 setup-node 的 npm 下载缓存，安装仍按 lockfile 执行 `npm ci`。
