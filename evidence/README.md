# 数据可靠性修复的离线证据

当前最终结果：原 56 项回归与 54 项收尾用例共 110 项，通过源码和干净目录 wheel 安装后复核。设备与部署未执行。

- `closeout/identity-final.json`：当前源码、wheel、基线身份和验证边界。
- `closeout/installed-results/wheel_identity.json`：7 份包内源码/标记与工作树逐字一致。
- `closeout/test-final.txt`、`closeout/installed-replay.txt`：最终运行摘要。
- `closeout/installed-results/resources.json`、`message_sizes.json`：主机有界资源和消息容量结果，不能外推为 Orin 实测。
- `closeout/dependencies-lock.txt`：本次 Python 3.12 隔离环境依赖；直接依赖仍遵循仓库 requirements.txt。
- `ws-data/baseline`、`ws-data/revision-baseline`、`closeout/before`：反例和回归所需的只读历史源码。
- `ws-data/wheel`：当前未发布候选，仍沿用 1.0.1，必须用 SHA-256 区分。

完整逐例输出、失败记录、相对基线与上轮候选的补丁、相对路径哈希清单和手动部署卡，见 [最终证据包](../deliverables/rm01OrinStatus-数据可靠性修复-证据包.zip) 与 [收尾报告](../deliverables/rm01OrinStatus-数据可靠性修复与验证.md)。报告及包记录发布前的本地开发验收状态；Git 提交/PR 发布不代表设备部署。

使用自己的隔离环境安装测试依赖及主机 C 编译器，然后从仓库执行：

```sh
python -m pytest -q tests/offline
python scripts/verify_ws_data_offline.py --output /tmp/ws-data-review-results
```

仅收集 `tests/offline`。旧 live 测试有固定局域网外联副作用，不能运行整个 `tests` 目录。离线测试只允许自己创建并回收的 Python 假输出/CLI 子进程与主机 C 读取程序；不会运行真实 tegrastats、ModelMonitor、systemctl、journalctl 或设备操作。
