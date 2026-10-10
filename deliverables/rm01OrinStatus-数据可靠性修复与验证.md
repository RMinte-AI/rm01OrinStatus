# rm01OrinStatus 数据可靠性修复与验证

WS-DATA-v1.0 / WS-DATA-CLOSEOUT-v1.0。状态：本地两项收尾完成，等待独立复审；用户在复审通过后手动部署。

## 产品结论

F1、F2均已关闭。采集进程创建成功但读取线程无法启动时，现在会回收进程并显式关闭无人接管的输出管道。CLI启动现在真正采用既有环境配置，配置预览、启动摘要和最终命令使用同一套选择与校验规则。

原数据身份、自洽快照、功耗解析、正常TJ及Socket.IO合同保留。原56项回归与新增54项收尾用例，共110项，在源码和干净目录安装后的候选上通过。没有发现本地新增兼容性阻断；尚未验证设备连续供温、实际最大报文和Orin资源开销。

本次有一项明确的部署行为变化：以前被CLI忽略的服务环境变量现在会生效。默认仍为1000ms采集、1s推送、debug=false，不能据此声称现场完全零变化。部署前必须核对服务真正收到的环境与命令行。

旧天山OS不认识fresh/seq，仍可能把缓存重发当作更新。本轮不改变其失温触发时机，不修改TJ绑定或风扇控制，不开始热模型项目。

## 起点、范围与身份

从已有返修工作树继续，HEAD仍为`4a765acc2ffe2a46135851f7b225c2856336cb9f`。开始时源码与文档已有未提交修改，均已保护并备份；没有从GitHub main重新开始，没有reset、clean、提交、推送或PR。

修改前四份关键源码哈希与附件一致；上轮ZIP指纹为`e0bc8a817129aaaecf3dcc8a6d8ca0f17157c6a68c41adff2bf8156717a9acc8`。原包和报告备份保留在本地evidence/closeout/before，最小包只带必要旧源码与指纹，不嵌套旧ZIP。

本轮生产修改仅在parser.py、config.py、cli.py；必要测试与接口/安装说明随附。server.py与上轮逐字一致，parse_line、get_snapshot、_accept_line三段函数的AST一致，原数据语义没有重做。包名版本沿用1.0.1，没有发布或打标签；原__version__差异仍是历史问题，身份必须用SHA确认。

当前wheel SHA-256：`b5f3520d1509a43601eed56fd00a81607581d3f41748d7a7e5c5352c0fd9677c`。前后源码、7份包文件核对和安装后导入位置见证据包evidence/closeout/identity-final.json与installed-results/wheel_identity.json。

## F1：输出管道归属与失败清理

原反例使用受控Python假输出进程与真实stdout管道：线程未启动，进程已回收、状态error，但管道仍打开。现在读者入口和启动失败路径以本生命周期的接管/取消状态协作：读者未接管时，失败路径取消迟到入口、回收对应进程、关闭管道并释放失败线程/进程引用；读者已接管时，仍由读者finally关闭，不在主线程抢关正在读取的流。

清理异常记录有限诊断，保留原启动异常和error/fresh=false。启动方关闭失败时保留待关闭对象供正常stop重试，不依赖GC，不宣称已经完全回收。没有修改Thread私有字段、换可重入锁或新增自动重启。原CLI退出意图/延迟清理协作保持。

| 检查 | 实际结果 |
|---|---|
| 线程构造失败、Thread.start在启动前失败 | 两类均用真实Python子进程及管道；进程回收、stdout关闭；外部强引用仍保留，未执行GC；error、seq=0、fresh=false正确 |
| 重复stop、重新正常采集 | 新stream读取新TJ；取消的旧入口迟到调用也不能接管旧管道 |
| 读者已接管后注入异常 | 只由读者线程关闭，主线程没有抢关；保持error |
| 进程清理/管道关闭异常 | 原启动错误不被覆盖，未完成资源状态真实；后续stop完成回收/关闭 |
| EOF、停止、读取异常、旧线程迟到 | 原回归保持通过 |
| 原七类CLI信号场景 | 退出码、状态和回收断言全部保留通过；信号只发给自建测试子进程 |

## F2：最终配置如何决定

每个字段按**显式CLI > 主环境变量 > 既有旧别名 > 默认**选择，之后才校验。合法高优先级可覆盖坏低优先级；非法主变量不回退到旧别名。Config()不隐式读环境；Config.from_env、CLI run和config预览共用小型入口。

| 字段 | 主变量 / 旧别名 | 默认、单位与实际消费者 |
|---|---|---|
| host | TEGRASTATS_API_HOST / TEGRASTATS_HOST | 10.10.99.98；非空主机名/地址，实际运行host |
| port | TEGRASTATS_API_PORT / TEGRASTATS_PORT | 58090；整数1..65535，实际运行port |
| debug | TEGRASTATS_API_DEBUG / TEGRASTATS_DEBUG | false；实际运行debug |
| update_interval | TEGRASTATS_API_UPDATE_INTERVAL / TEGRASTATS_UPDATE_INTERVAL | 1.0秒；有限正数，生产推送循环sleep |
| max_connections | TEGRASTATS_API_MAX_CONNECTIONS / TEGRASTATS_MAX_CONNECTIONS | 10；正整数，实际连接限制器 |
| tegrastats_interval | TEGRASTATS_API_TEGRASTATS_INTERVAL / **TEGRASTATS_INTERVAL** | 1000毫秒；正整数，Parser和最终命令 |
| cors_origins | TEGRASTATS_API_CORS_ORIGINS / TEGRASTATS_CORS_ORIGINS | *；非空，Flask CORS/Socket.IO；HTTP响应头已核对 |
| log_level | TEGRASTATS_API_LOG_LEVEL / TEGRASTATS_LOG_LEVEL | INFO；DEBUG/INFO/WARNING/ERROR，归一化并进入实际日志级别 |
| log_file | TEGRASTATS_API_LOG_FILE / TEGRASTATS_LOG_FILE | app.log；显式Config可为None；**没有文件日志消费者**，摘要标注未接线 |
| allow_unsafe_werkzeug | TEGRASTATS_API_ALLOW_UNSAFE_WERKZEUG / TEGRASTATS_ALLOW_UNSAFE_WERKZEUG | true；进入实际构造/运行许可，保持既有默认策略 |

环境布尔支持true/false、1/0、yes/no、on/off，忽略大小写与两侧空白；拼错或空值报错。--debug、--no-debug显式覆盖环境，未传时保留环境；同传时最后一个生效并显示对应来源。1000.0归一化为1000ms，不能乘1000。推送的小数秒继续合法；采集小数毫秒、零、负数、nan/inf仍拒绝。

run和config预览接受同一组相关选项。摘要显示监听、采集ms、推送s、共享过期界限、debug、连接数、日志级别及来源。CORS/文件日志不回显任意环境内容。非法配置在服务构造/采集/监听前退出，错误包含字段、来源及类型要求，不打印环境原值。

config明确写“本次环境与参数对应的启动配置；未查询运行中的服务”，不创建服务对象、采集或监听。帮助/版本查询不解析坏环境。测试先清理相关TEGRASTATS环境，再注入输入，不依赖开发机环境恰好为空。

| 场景 | 实际贯通结果 |
|---|---|
| 无环境、无选项 | 命令1000ms，推送1s，默认不变 |
| 仅主3000或旧采集别名3000 | 命令3000，阈值9000；来源正确 |
| 主3000＋旧2000 | 主3000生效 |
| 环境3000＋CLI1000/1000.0 | CLI1000生效，阈值5000 |
| 坏环境＋合法CLI | 采集、host/port/debug/推送/连接数/日志级别正确覆盖 |
| 主非法/空，旧合法 | 按主值报错，未创建服务/采集/监听，不回退 |
| debug环境与显式开关/双开关 | 实际监听参数和来源吻合，最后一个开关生效 |
| 全十项主变量/旧别名 | 解析、来源与可用消费者一致；文件日志边界明确 |
| 相同环境/选项的预览与run | 值、来源一致；预览零构造、零采集、零监听 |
| 重复调用、help/version与坏环境 | 不沿用前次配置，帮助/版本可用，无真实设备副作用 |

## 原合同、回归与证据边界

实际主机为macOS arm64、Python3.12.3，直接依赖沿用requirements.txt固定版本；完整锁文件随包。110项在源码和干净安装候选后均通过，另通过真实C读取核心、Socket.IO Packet容量和有界资源复核。Python3.8仅做语法检查，没有该版本运行或Orin实测声明。

仅收集tests/offline。网络/监听、真实采集/ModelMonitor被拒绝；例外仅为测试自建的受控Python假输出/CLI子进程，以及主机C编译/读取程序，均有期限和回收。没有运行live脚本、整个tests目录、安装脚本、systemctl/journalctl或真实tegrastats。

数据身份、单调年龄、阈值边界、UTC跳变、同值新输入、日志噪声、缺项、并发深复制和重启迟到仍通过。重发保持stream/seq/received_at，同值新输入仍增序号。fresh不保证字段齐全、硬件准确或无管道/网络延迟；health的healthy仅表示HTTP可响应。

功耗只收合法VDD.../VIN_...完整mW或已有无单位mW对，完整原名转小写。零有效；缺失/冲突/不支持单位省略；RAM/SWAP不混入、不求总功耗。正常CPU/内存/GPU及实际temperature.tj值、类型和路径保持；生产HTTP路由和Socket.IO事件回归通过。

客户端指定版本的C读取/JSONPath在主机替身中仍读到同一TJ。旧客户端不自动拒绝过期缓存；原CPU/内存/功耗别名不匹配和零填充仍为历史限制。典型报文约1.1KB；内置监控4096字节单块、没有同样重组，自动化8192字节重组含终止符。超限不能保证设备接收，不为合成rail修改天山OS。

当前报文大小、解析/序列化耗时及有限帧内存对照见installed-results/resources.json与message_sizes.json；只属于开发机，不是Orin保证或传感器校准。

失败未删除：初批97/108项通过；重复调用用例曾因共享夹具未导入出现108通过＋1设置错误，修正导入并保留日志，没有放宽断言。原弃用警告与更早失败通过历史包指纹保留。最终依据只看本轮test-final.txt、installed-replay.txt及当前身份。

## 用户手动部署与回退卡（复审通过后执行）

以下步骤本轮均未执行。先独立复审，再由用户在受控待机窗口操作；现场路径用明确占位符，不猜目录。不要在高负载制造供温中断，不关闭风扇或更改既有失温策略。

1. 登记实际服务名、解释器/CLI路径、ExecStart、User、WorkingDirectory、环境和EnvironmentFile、旧包/源目录。备份旧wheel或实际安装代码及对应包元数据、服务命令和环境文件，记录SHA，写明唯一回退对象。只有源码安装时备份实际目录及启动方式，不能把未包含现场安装内容的checkout当回退包；环境备份在现场保管，不需上传。
2. 按服务真实环境和参数预览，核对值与来源，特别找出“旧CLI忽略、现在生效”的变量。继续采用现有约定频率；1ms只是测试值，不是部署建议。终端config不证明systemd环境相同。
3. 复制候选wheel到用户选定路径并核对SHA。待机停服务后，用服务实际解释器强制重装同版本新包，避免版本判断跳过，不顺带升级依赖。如果服务直接导入工作目录源码，须处理导入优先级，不能让旧src遮住新wheel。

```sh
# 用户填写，复审通过后在现场执行；不是已验证的现场路径。
SERVICE='<实际WS服务名>'
WS_PY='<该服务Python解释器绝对路径>'
CANDIDATE_WHEEL='<新wheel绝对路径>'
BACKUP_DIR='<旧包/代码和服务环境备份目录>'
sha256sum "$CANDIDATE_WHEEL"
# 应为 b5f3520d1509a43601eed56fd00a81607581d3f41748d7a7e5c5352c0fd9677c
# 完成备份与配置核对后，在待机窗口执行：
sudo systemctl stop "$SERVICE"
"$WS_PY" -m pip install --no-index --no-deps --force-reinstall "$CANDIDATE_WHEEL"
```

4. 在服务实际工作目录、解释器及导入环境下核对模块路径和SHA，与identity-final.json逐项比较，不能只看1.0.1。仅导入不会启动采集。

```sh
"$WS_PY" - <<'PY'
import hashlib
from pathlib import Path
import tegrastats_api.parser as parser
import tegrastats_api.server as server
import tegrastats_api.cli as cli
import tegrastats_api.config as config
for module in (parser, server, cli, config):
    path = Path(module.__file__)
    print(path, hashlib.sha256(path.read_bytes()).hexdigest())
PY
# 确认来源与SHA正确后，按原服务配置启动：
sudo systemctl start "$SERVICE"
```

5. 从真实启动日志的最终摘要核对地址/端口、采集ms、推送s、阈值、debug及来源。确认天山OS仍收到实际TJ、WS恢复，stream/seq随新输入推进，age/fresh与状态合理，功耗合法且不夹带RAM/SWAP。保存一小段可复核的新元数据/功耗记录，核对正常报文大小和基本资源。正常TJ/WS仅证明主路径恢复，不等于热模型有效。
6. 持续无温度、不能启动、配置意外变化或明显异常时，停止继续测试，恢复第1步明确备份的旧wheel（同样强制重装）或实际代码及包元数据，同时恢复原服务命令/环境，再按原方式启动。核对旧导入路径/SHA与TJ恢复；不擅自改风扇/保护策略。没有可恢复旧版本时不进入安装步骤。

本轮未连接设备、安装到Orin、重启真实服务、修改天山OS、调风扇、运行CUDA、创建/训练热模型或提交推送。交回证据后停止，等待独立复审和用户手动部署。
