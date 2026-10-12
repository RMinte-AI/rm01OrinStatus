# 监控服务 1.1.1：Orin / Thor 离线候选

共同合同为 [TTO-PLATFORM-CURVE-v2.0](ORIN_THOR_CONTRACT_V2.md)。服务只从本机设备树的 NUL 分隔 token 确认 tegra234/tegra264；证据缺失或冲突为 unknown。平台身份在采集生命周期内固定，主机名、温度字段和旧 GR3D token 都不能代替设备树证据。

Orin 从同一 tegrastats 行取得 GR3D 利用率；Thor 使用独立 NVML 读取线程。合法 0% 保留；读取错误、不可用或过期为 null，失败不延续旧成功值。温度、完整功耗 rail 名称、current/average 整数 mW 和正常 TJ 接口保留，不添加功耗合计或伪造别名。

源周期仍默认 1000 毫秒。CLI 的 1000、1000.0 和环境设置统一归一化；1 始终为 1 毫秒。优先级为显式 CLI > TEGRASTATS_API_* > 旧 TEGRASTATS_* > 默认；预览、启动摘要、命令及 telemetry_meta.source_sample_interval_ms 一致。Thor GPU 独立默认 1000 毫秒，不能从源周期推算。GPU 超时为 max(5000, 3×GPU周期)。

## 原子样本和清理

完整源行接收时，在解析之前选择同生命周期、完成时刻不晚于本行的最近 GPU 结果。成功、失败和能力结果都参与选择，不能越过新错误寻找旧成功。缓存保留一个新鲜度窗口加前驱：ceil(timeout/interval)+1 个结果，默认 6 个；耗尽时为 unavailable，不用未来值或截断负年龄。

platform、gpu、测量和源周期随 stream/seq 封存。GPU 单独更新不产生主样本；缓存重发不递增 seq；相同数值的新源行递增 seq。无有效源行时仍为空、503、seq=0。HTTP/WS 不查询 NVML，分类 API 保留原投影，health 的实时能力诊断另列。

NVML 延迟导入，只接受单 GPU 索引 0，记录 UUID；多 GPU、MIG 和不支持的利用率明确不可用。初始化成功才配对 shutdown。信号只记录退出请求，正常控制流清理；一个组件失败仍尝试其余清理，启动原错不被二次清理错误覆盖。输出管道按生命周期 owner 关闭。

GPU stop 最多等待 2 秒，不能取消 native 调用。超时为 cleanup_pending，释放失败为 cleanup_error，保留原 owner；旧线程/会话未释放时不创建第二 GPU 线程，源采集可以继续，GPU 为 error。晚到结果不得污染新 stream。若目标驱动暴露这个限制，应阻止发布并返回复审。

## 候选安装和身份

本 PR 交付修复后的源码。此前本地 1.1.1 wheel/证据包未包含最后两项退出修复，不能用于验收本 PR；后续安装必须从本 PR 的确定提交重新构建 wheel。


使用交付 wheel 非 editable 安装。基础 Python>=3.8 保留；实际本地运行版本在报告和依赖锁中列明，语法检查不能冒充 Python3.8 运行验证。Orin 可以不安装 NVML；Thor 可选绑定固定 nvidia-ml-py==13.615.71。目标 Python、驱动与真实 NVML 支持仍须现场验证。

scripts/install_candidate.py 只接受明确 wheel、SHA256、目标解释器和新记录文件，使用 --no-index；它不改 systemd、不启动服务、不查询 GPU。依赖已备齐时可不传 wheelhouse；Thor extra 必须提供离线 wheelhouse。示意命令由用户在复审通过并安排窗口后执行：

```sh
python scripts/install_candidate.py --python /明确环境/bin/python --wheel /候选路径/tegrastats_api-1.1.1-py3-none-any.whl --sha256 从交付清单复制的SHA256 --output /保存位置/install-record.json
# Thor：另加 --thor --wheelhouse /已为目标Linux/Python准备并核验的离线依赖目录
```

交付的依赖 wheelhouse 用于 macOS ARM64 / Python3.10 本地复核，其中平台相关 wheel 不能直接装到 Orin/Thor。设备依赖须在部署前单独准备、锁定并确认；不要让安装器联网猜版本。

安装器核验包版本、dist 版本、实际导入路径及所有 Python 文件与 wheel 一致，并拒绝 editable/shadowed import。可再用同解释器执行 scripts/check_install_identity.py --wheel ... --source ... --output 新文件。包安装成功记录明确保留 running_service_verified=false。

已有 scripts/install.sh 会配置、启动真实服务，本轮未运行；它不作为这次候选身份验收入口。已有 systemd override 不自动删除。用户现场分别读取 systemctl cat 服务名 和 systemctl show 服务名 -p ExecStart -p FragmentPath -p DropInPaths -p MainPID，确认有效命令指向已核验环境。启动后再比对 /proc/PID/exe、/proc/PID/cmdline、实际包路径与 /api/health 的版本/路径。磁盘新包、healthy、API 新字段以及旧 PID 都不能单独证明新包生效；health 路径是辅助证据。保存完整命令和输出，不凭 unit 文件模板推测有效 ExecStart。

## 用户手动部署与回退顺序

1. 独立复审通过后安排窗口。先备份有效 unit/drop-in、环境变量和配置，保存旧环境路径、wheel/依赖及 SHA；确认来源、磁盘空间与版本。不要覆盖旧环境。
2. 在新的目标环境安装候选，先完成上述离线身份核验及实际驱动兼容确认。仅在计划的窗口由用户手动切换有效 ExecStart 到新环境并重启原服务；保留原网络、TJ 和风扇配置。
3. 核对新 PID/解释器/版本/路径，再验证设备树、Orin GR3D 或 Thor UUID/NVML、源周期和 GPU 独立周期；记录至少 180 秒有界观察的 CPU/RSS、查询耗时/漏期、正常 TJ 与 HTTP/WS。错误、0%、重启、清理和超时按共同合同验收；资源及驱动证据未通过不得宣布发布成功。
4. 失败时保留候选日志，用户手动恢复备份的有效 ExecStart、drop-in/配置和原环境，再重启原服务；核对回退 PID、解释器、旧 wheel SHA 和正常 TJ。不删除记录，不给未知 GPU 填零，不在回退时升级旧配置。

本轮只交本地候选，未连接设备、运行真实采集、安装到计算模块或重启服务。TTO 的 consumer/model/report 验收另由 TTO 会话给出，不能用本仓测试替代。
