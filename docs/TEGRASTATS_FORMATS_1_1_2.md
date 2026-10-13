# 1.1.2：Conda 入口与 tegrastats 格式兼容

本版本在 1.1.1 的数据身份、快照、平台识别和退出清理修复基础上，仅修复 Conda 查找与加载顺序，以及新版 tegrastats 温度、功耗格式的解析。HTTP 路由、Socket.IO 事件、数据合同和采集周期不变。

## Conda 入口

安装脚本不再在加载 Conda 之前要求它已位于 PATH，也不再执行 `conda init` 后要求用户重新打开终端。`init_conda` 优先使用 PATH 中的 conda；找不到时依次检查 `$HOME/miniconda3/bin/conda`、`$HOME/anaconda3/bin/conda`、`$HOME/miniforge3/bin/conda`。

选定工具后，调用 `conda shell.bash hook` 并在当前脚本中加载，供后续 `conda activate` 使用；不写入 `.bashrc` 或 `.profile`。未找到工具、hook 执行失败或加载失败时退出码为 1。原有环境复用、创建、安装与服务流程保留。

## 格式与接口映射

| 输入格式 | 含义 | 现有接口取值 |
| --- | --- | --- |
| `tj@56.968C` | 当前温度 | `temperature.tj = 56.968` |
| `tj@56.968C/61.25C` | 当前温度 / 本次采集进程累计最高温度 | 仍取首值；保持浮点数 |
| `VDD_GPU_SOC 5147mW/4900mW` | 当前功耗 / 平均功耗 | `current = 5147`、`average = 4900`，整数 mW |
| `VDD_GPU_SOC 5147mW/4900mW/6000mW` | 当前功耗 / 平均功耗 / 本次采集进程累计最高功耗 | 仍取前两值；不增加最高值字段 |

保留原有无单位功耗 token，例如 `5147/4900`；新增三值格式同样要求所有位置使用一致单位。残缺值、错误后缀、额外值和不一致单位不会被截取为正常功耗；冲突的重复 rail 继续被丢弃。缺失功耗保持空对象，不补零、不沿用上一帧。

## 新增字段的取值依据

静态核对了 NVIDIA 官方发行包中的 `/usr/bin/tegrastats`，未安装或执行该工具：

- 所检查的 36.4.7、38.4.0 工具包含单值温度、两值功耗输出格式。
- [39.2.1 官方工具包](https://repo.download.nvidia.com/jetson/som/pool/main/n/nvidia-l4t-tools/nvidia-l4t-tools_39.2.1-20260806224157_arm64.deb)包含 ` %s@%gC/%gC` 和 ` %s %ldmW/%ldmW/%ldmW`。
- 温度输出路径 `print_elements` 的 `0x5cf0–0x5d48`：首值取本次传感器读数；第二值通过比较、保存 `max(previous_max, current)` 得到。
- 功耗 INA2xx 路径 `0x5fc8–0x6058`：均值按 `average += (current - average) / count` 更新，最高值另行比较保存；输出参数依次为 current、average、maximum。INA3221 和直接电源读取路径顺序相同。

[39.2 官方说明页](https://docs.nvidia.com/jetson/archives/r39.2/DeveloperGuide/AT/JetsonLinuxDevelopmentTools/TegrastatsUtility.html)仍描述旧格式；新增最高值的结论来自上述发行包的计算及输出路径。现场示例与之相符，但不能据此声称已验证现场所装工具的确切版本。

## 本地验证与边界

当前源码构建的 1.1.2 wheel 在临时隔离目录通过原有 170 项回归及新增 12 项测试，共 **182 passed、0 failed、0 skipped**；测试环境为 Python 3.12.3。新增测试覆盖 Conda 的 PATH 优先级和三个用户安装目录、找不到工具、不同数值的格式映射、错误 token，以及采集替身到 HTTP / Socket.IO 的输出。

数据身份的贯通验证确认：缓存重复发送不推进序号；相同数值的新输入仍推进序号；后续帧缺失功耗时 HTTP 与 Socket.IO 均返回空功耗。原回归继续覆盖并发快照、重启隔离、信号退出、输出管道所有权、GPU 释放重试及 CLI 配置。

所有验证均为本地离线验证；Conda 使用命令替身，采集进程和 GPU 使用替身，HTTP / Socket.IO 使用内存测试客户端。真实 Conda 环境、现场工具输出、驱动和服务退出行为仍需手动真机验收。没有连接设备、启动真实采集、部署、重启服务或修改天山 OS、TJ 绑定、风扇与热模型。

已有本地测试依赖时，在仓库根目录运行：

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/offline
```

请勿据此盲跑整个 `tests/` 目录或执行安装脚本的真实部署流程。
