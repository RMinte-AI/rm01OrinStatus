# 数据身份与功耗合同（WS-DATA-v1.0）

`/api/status`、CPU/内存/温度/功耗分接口及 Socket.IO `tegrastats_update` 在原顶层增加 `telemetry_meta`。原数据路径、事件、连接/心跳和正常更新间隔不变。顶层 `timestamp` 仍为 UTC 响应/发送时间字符串。

| 字段 | 类型 | 含义 |
|---|---|---|
| schema_version | int | 1，元数据合同版本 |
| stream_id | string | 每次采集生命周期的随机 UUID；不是连接或主机启动身份 |
| sample_seq | int | 每接受一条有效输入加 1；首帧为 1，无帧为 0 |
| received_at | string / null | 完整行被读到的 UTC 时刻 |
| sample_age_ms | number / null | 单调时钟计算的接收年龄；无法确定时 null |
| fresh | bool | 采集 running 且有样本、年龄小于界限 |
| collector_state | string | starting / running / stopped / error |
| freshness_timeout_ms | positive number | max(5000, 3 × 采集间隔 ms) |
| time_basis | string | collector_receive |

阈值相等即过期。UTC 校时不参与年龄计算。EOF、停止、异常退出及时使 fresh=false。重启清空缓存并从新身份的序号 0 开始；旧线程结果不可发布到新生命周期。客户端重连不改变采集身份。同内容的新输入增加序号，重发不增加。

帧识别允许以 RAM、SWAP、CPU、GR3D_FREQ、温度令牌或明确供电名称开头，可有仓库示例的日期前缀；还必须成功解析至少一类字段。空行、日志前缀、完全损坏的数据不发布。允许只含有效温度的帧。新帧不补用旧字段。

数据与身份在同一锁下深复制；所有服务出口使用同一快照规则。独立 HTTP 请求可能属于不同帧。解析器 `get_current_status()` 保留原数据结构、数值时间戳与无数据 `{}` 行为；`get_snapshot()` 返回 `(data, meta)`。`parse_line()` 对不可识别/完全损坏行返回 `{}`，不再制造空容器假帧。

无首帧的数据接口仍为 503，并附元数据。健康接口 `status=healthy` 仅表示 HTTP 可响应，可查看其元数据了解采集状态。fresh 不保证每个字段存在、传感器准确或上游没有管道积压。

功耗只接受 `VDD...`、`VIN_...` 的完整数字对，两侧同时带 `mW` 或同时无单位；无单位采用已有 mW 语义。名称转小写，`current`/`average` 保留上游值，`unit=mW`。明确 0 是有效值。RAM/SWAP 不再进入 power。负数、小数、混合单位、mA/W/MB、尾缀、NC 或冲突重复项被省略；相同的重复读数只输出一次。无别名、无整机求和。异常供电最多每分钟记录五条通用诊断，不保存原始行。

旧客户端可忽略元数据继续读取 `temperature.tj`。本轮继续发送最近缓存，不更改旧客户端失温触发时机。旧客户端可能把重发缓存当作更新；新消费者需同时检查 stream/seq、fresh 和所需字段。

格式依据：[NVIDIA r36.4.3](https://docs.nvidia.com/jetson/archives/r36.4.3/DeveloperGuide/AT/JetsonLinuxDevelopmentTools/TegrastatsUtility.html)、[NVIDIA r38.4](https://docs.nvidia.com/jetson/archives/r38.4/DeveloperGuide/AT/JetsonLinuxDevelopmentTools/TegrastatsUtility.html)。元数据与新鲜度界限是本项目设计，未声称为 NVIDIA 的测量保证。

离线验证只指定 `tests/offline`。已有 `test_simple_ws.py`、`test_websocket.py` 有导入即外联行为，`test_websocket_debug.py` 主入口也连接真实地址；不能收集整个 tests 目录。候选包独立重跑入口为 `scripts/verify_ws_data_offline.py`，只安装本地 wheel，不启动服务或真实采集。

独立复审返修：CLI退出信号只登记退出意图；服务启动/清理期间延后退出，在正常执行路径停止组件。运行中以SystemExit唤醒阻塞服务循环，finally清理。重复退出不在信号处理器里取得解析器生命周期锁或清理锁；采集进程创建返回后才进行正常清理。退出意图阻止服务再次启动，清理保留error状态。

CLI/Config在入口归一化正整数毫秒，1000.0等整数等值输入得到1000。Parser仍只接受正整数int，命令为`tegrastats --interval 1000`，默认保持1000ms。阈值由同一整数计算；小数毫秒、零/负数、非有限值拒绝。旧CLI“秒”帮助错误已修正，没有把透传数值乘1000。

新增交叉测试仅在测试创建的临时Python子进程中发送SIGTERM给自身PID；子进程中的网络和真实Popen均被禁用，采集/模型启动与网络监听由替身取代。它验证实际CLI/server/parser协作，不是设备退出或部署证明。

部署前收尾（WS-DATA-CLOSEOUT-v1.0）：启动失败路径只关闭尚未由读者接管的stdout；读者入口和失败路径用同一生命周期的接管/取消状态协作，迟到的取消入口不能读取旧管道。读者已接管时仍由其finally关闭。未成功关闭的启动方流保留引用供正常清理重试；有限诊断保留原启动错误和error状态。

CLI run和config预览现在复用Config.resolve：显式CLI > 主环境变量 > 原旧别名 > 默认；先选定，再校验。Config()不会隐式读环境，Config.from_env使用同一解析规则。配置预览不是运行服务状态。采集为正整数ms，推送为有限正数s；端口1..65535，连接数为正整数。环境布尔支持true/false、1/0、yes/no、on/off（忽略大小写及外侧空白）。--debug与--no-debug未传时保留环境，两者同传时最后一个生效。所有十项既有环境名称/别名保持；主采集变量的旧别名仍是TEGRASTATS_INTERVAL。

选中非法值在服务构造前退出，说明字段、来源及类型要求，不回退、不打印环境原值。help/version不解析坏环境。最终摘要由同一配置及共享过期界限规则生成；显示监听、采集、推送、debug、连接数、日志级别和来源。CORS传入Flask/Socket.IO；log_file仍没有文件日志消费者，摘要明确未接线。本次开始尊重环境是有意行为变化：部署前必须比较服务真正接收到的环境和命令行，不仅比较默认值或用户终端预览。
