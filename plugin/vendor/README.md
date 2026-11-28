# Crew 后端内置依赖

| 包 | 版本 | PyPI sdist | SHA-256 | 许可证 |
| --- | --- | --- | --- | --- |
| zeroconf | 0.154.0 | [zeroconf-0.154.0.tar.gz](https://files.pythonhosted.org/packages/d6/dd/b1eb6a87dc0376289fb78c13231c4702797da49dd0da062c5d46a4cff865/zeroconf-0.154.0.tar.gz) | `830315497af9bbd75924c2218cd3b64f0bde0e808ee316aabb5d9288836c0c43` | [LGPL-2.1-or-later](LICENSES/zeroconf-LGPL-2.1.txt) |
| ifaddr | 0.2.0 | [ifaddr-0.2.0.tar.gz](https://files.pythonhosted.org/packages/e8/ac/fb4c578f4a3256561548cd825646680edcadb9440f3f68add95ade1eb791/ifaddr-0.2.0.tar.gz) | `cc0cbfcaabf765d44595825fb96a99bb12c79716b73b44330ea38ee2b0c4aed4` | [MIT](LICENSES/ifaddr-MIT.txt) |

Crew 后端没有第三方依赖安装步骤。Windows 和 macOS 桌面包通过现有的 `extraResources` 把 `plugin/` 拷进 `backend/`，其中包含 `vendor/`。局域网公告首次用到 zeroconf 时，`crew/discovery.py` 才把这个目录加入 Python 导入路径。

zeroconf 0.154.0 支持 Python 3.11+，其运行元数据只要求 `ifaddr>=0.1.7`。ifaddr 0.2.0 没有其他运行依赖。这两个版本无需 `async-timeout`。Cython 仅用于构建可选扩展。

随包分发的 zeroconf 是未修改的纯 Python 源码和 LGPL 原文。源码以独立 Python 包装载，用户可替换 `backend/vendor/zeroconf/`，重启 Crew 后使用替换版本。

升级时从 PyPI 获取 sdist，核对 PyPI JSON 的 SHA-256 与运行依赖，替换 `src/zeroconf/` 和 `ifaddr/` 中的源码、类型声明及许可证。保留纯 Python 装载方式，排除编译制品和生成的 C 代码，更新本表，再验证干净 Python 进程的导入、注册、发现、注销与关闭。
