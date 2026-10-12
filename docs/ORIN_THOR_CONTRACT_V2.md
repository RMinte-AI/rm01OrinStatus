唯一共同接口合同版本为 `TTO-PLATFORM-CURVE-v2.0`。以下规则在两份方案中保持逐字一致，作为后续实施和交叉测试的共同依据；用户现已授权分仓本地实施：WS 会话只负责 rm01OrinStatus，TTO 会话只负责 TTO；接口及候选仍须按证据验收。后续必要变更必须同步两份方案及 fixture，不能分别改名或改变时间含义。

新增合同采取加法扩展；既有 `telemetry_meta.schema_version=1`、`stream_id`、`sample_seq`、`time_basis=collector_receive` 和响应时刻的顶层 timestamp 语义保持不变。新增版本号不重定义既有 schema1。

| 字段 | 含义、类型和约束 |
| --- | --- |
| `platform.schema_version` | JSON 整数 1；与 telemetry_meta 和 gpu 的版本分开，bool 或 1.0 不能冒充整数版本 |
| `platform.family` | `jetson_orin`、`jetson_thor` 或 `unknown` |
| `platform.soc` | Orin 为 `tegra234`，Thor 为 `tegra264`，unknown 为 null；family/soc 必须一致 |
| `platform.identification_source` | 已确认时为 `device_tree_compatible`，unknown 为 null；不能无证据声明识别成功 |
| `platform.compatible` | 实际读取的 NUL 分隔 token 列表，用于追溯；在生命周期内封存，不从 model/hostname 生成 |
| `platform.gpu_backend` | 本生命周期选定的策略：Orin 为 `tegrastats`，Thor 为 `nvml`，未知为 `unavailable`；缺依赖和临时故障不改此身份 |
| `gpu.schema_version` | 新增 GPU 合同的 JSON 整数版本，首版为 1；不能用 gpu 对象是否存在来区分新旧合同 |
| `gpu.utilization_pct` | valid 时为有限 JSON 数字，0 至 100，允许整数或小数；其他状态为 null。bool、字符串、NaN、Infinity 和 MHz 不合法 |
| `gpu.source` | valid/stale 时为该结果来源，error 时为失败的选定后端，均为 `tegrastats` 或 `nvml`；unavailable 时为 `unavailable` |
| `gpu.status` | 封存时的 `valid`、`unavailable`、`error` 或 `stale`；关联字段组合见下表，重发时不修改 |
| `gpu.sampled_at_utc` | valid/stale 时为成功读取的 ISO8601 UTC 字符串（Z 或 +00:00）；Orin 以同一源行接收近似起算，NVML 以查询成功返回完成起算。error/unavailable 为 null，失败尝试时间另记诊断 |
| `gpu.age_at_telemetry_receive_ms` | valid/stale 时为成功结果相对该源行接收时刻的年龄，用采集端同一单调时钟计算，为有限非负数字；error/unavailable 为 null |
| `gpu.sample_interval_ms` | 选定后端的实际生效读取调度周期，正整数毫秒；Orin 等于源周期，Thor 独立。它不是成功读数之间的实测间隔，也不是 NVML 硬件统计窗口；无选定后端为 null |
| `gpu.freshness_timeout_ms` | 选定后端的正整数毫秒超时，固定为 `max(5000, 3 × gpu.sample_interval_ms)`；无选定后端为 null |
| `gpu.device_uuid` | NVML 已选定设备的稳定 UUID，未选定或 tegrastats 路径为 null；选定后失败仍保留，不换设备。null 到首个 UUID 是首次确认，不是平台切换 |
| `telemetry_meta.source_sample_interval_ms` | 新增正整数毫秒字段，来自实际启动 tegrastats 的归一化 parser.interval；schema_version 仍为 1，不能用 GPU 周期或 timeout 推算 |

选定后端暂不可用时，GPU 周期和超时仍记录本生命周期已生效的调度设置，不代表读取已成功；实际尝试/成功间隔、耗时和能力原因记录在诊断中。未知平台没有选定 GPU 后端，其 GPU 周期和超时为 null，源周期仍按实际 tegrastats 命令发布。功耗继续为整数 current/average、unit=mW，温度继续为原名称对应的数字，不新增 rail 合计或别名。

**新旧合同判别与旧字段**

1. 任一 platform 字段、gpu.schema_version 或统一 GPU 字段出现，即进入新合同检查；缺标志、类型错误、未知版本和缺必需字段都报告合同错误，不能转入旧路径掩盖问题。统一 GPU 字段指上表 gpu 下除 schema_version 外的字段，包括 device_uuid；platform=null 或错误类型也不是字段缺失。已知 family/soc/backend 必须符合上表，valid/stale/error 的 gpu.source 必须匹配选定后端。
2. 只有 platform 缺失、gpu.schema_version 缺失且没有任何统一 GPU 字段的明确旧结构，才允许 legacy_v1 兼容。旧 gpu={} 或 gpu 缺失表示 GPU 缺失；旧 gr3d_freq 的合法 0% 仍有效，平台保持未确认。不能因旧字段形似 Orin 就确认硬件。
3. 既有 gpu.gr3d_freq 仍表示真实 tegrastats token 的利用率百分比，不改成 MHz，不从 NVML 值生成。统一来源为 tegrastats 且旧百分比有效时，两字段必须一致；不同来源不强求数值相等。Thor 若实际出现旧 token，可以保留原始解析结果，统一 GPU 仍按选定 NVML 策略读取，不增加 fallback 链。
4. 新 GPU 的 error/stale/unavailable 或非法结构不能回退到旧字段。普通 GPU 合同/能力问题撤下 GPU 值并记录原因，保留其他有效输入；样本身份冲突则按既有隔离和断段规则处理。

**GPU 状态与恢复规则**

| 情形 | status / source | utilization、时间与年龄 |
| --- | --- | --- |
| 最近符合因果条件的成功结果，年龄小于 timeout，含合法 0% | valid / 结果后端 | utilization 为真实数字；成功 UTC 和非负年龄均必需 |
| 依赖缺失、指标不支持、Orin token 缺失、未知平台、尚无首个结果，或有界历史中无符合 t 的结果 | unavailable / unavailable | utilization、sampled_at_utc、age 全部为 null；诊断记录具体原因 |
| 初始化、worker 启动或读取失败；选定后端的异常结果是 t 前最近结果 | error / 失败后端 | utilization、sampled_at_utc、age 全部为 null；不得继续提供前次成功值或刷新其时间 |
| t 前最近结果曾成功，但其年龄已达到或超过 timeout，且没有更晚的错误结果 | stale / 成功后端 | utilization 为 null；保留那次成功 UTC 和真实年龄以便追溯 |

成功、错误和能力结果均有内部完成单调时刻与生命周期标识。按完成时刻选择结果时不得越过最近错误重新挑选旧成功值。error/unavailable/stale 只有在新的成功 GPU 结果进入后续新的源样本时才恢复为 valid；不会改写已封存样本。不可用原因和 worker 清理状态通过启动、运行和退出诊断表达，不把实时诊断写进已封存的 gpu 对象。

**因果合并、身份与独立新鲜度**

Thor 源完整行接收时先取得同一生命周期且完成时间不晚于 t 的 GPU 结果副本，或使用有界历史按 t 选择最近结果，再解析和原子提交。成功、错误和能力结果都参与选择；只有 latest 单槽、解析后再过滤不满足要求。例如 A 在 9.9 秒完成，源行 10.0 秒接收，B 在 10.1 秒覆盖 A 后才提交：本帧不能使用 B，有符合条件的 A 时必须能取到 A；历史耗尽则诚实记录 unavailable，不能把负年龄截为 0。Orin 从同一源行解析 GPU 百分比，不新增异步 GPU 缓存；以该行 t/UTC 近似起算。GPU 查询不在 parser 数据锁、HTTP 或 WS 路径执行，结果选择及复制只持短锁，有界历史及容量写入实现说明和测试。

每个 stream_id/sample_seq 封存全部测量字段、platform、gpu 和 source_sample_interval_ms。只有外层 sample_age_ms 和响应 timestamp 按原合同更新。NVML 单独完成读取不产生主样本或递增 seq；相同数值的新有效源行仍产生新 seq。停止/重启创建新生命周期和 stream，旧 GPU/源线程晚到的结果不得发布、进入新缓存或修改新会话资源。

TTO 在重复样本返回之前，将 platform 全对象和 source_sample_interval_ms 纳入不可变内容校验，gpu 全对象继续纳入指纹。同 key 变化是 identity_conflict；同一 stream 跨 seq 的 platform、源周期或 GPU 调度周期/超时设置变化是生命周期合同违例，记录事件、断段并停止不匹配模型，保留原始证据。GPU 状态/读数可在新 seq 合法变化，设置不能随故障改变。不能把这些违例降格为普通 duplicate 或 GPU 缺失。

TTO 首次接收时以 gpu.age_at_telemetry_receive_ms + telemetry_meta.sample_age_ms 作为 GPU 年龄下界，之后推进 N305 单调时间；重复样本沿用源年龄单调下界锚点及既有 max 推进规则，不能倒退或重复加本地经过时间。它不包含未测网络在途时间，不能跨机器减 UTC。年龄达到 timeout 即不再可用；即使封存 status=valid，消费者也必须计算接收/使用时的有效状态，不回写原文。GPU 单独过期撤下 gpu_busy 及依赖它的模型输出，不使新鲜 TJ 自动失效。Orin 的年龄以源行接收近似起算，NVML 的读取完成时间也不是硬件测量的精确时刻或统计窗口。

新服务的源周期来自最终 tegrastats 命令。TTO 在输入配置记录该值及来源，已有人工 source_sample_interval_ms 只用于明确旧合同；新服务与非空人工值冲突时报告配置冲突，不静默覆盖。旧合同缺周期且无可信人工配置时保持未知，不反推或授予需要已知周期的正式数据集/模型资格。源周期和 GPU 周期分别保存，不增加特征列。

**生命周期协作与接口范围**

NVML 会话由一个生命周期内的 GPU 读取组件拥有，初始化/查询在其唯一 worker 中执行，延迟导入可选绑定，成功初始化才由该 owner 配对释放。停止请求唤醒周期等待；初始化、查询和释放不占 parser 数据锁，join 时不能持有 worker 发布/退出所需的锁。信号只记录退出意图，正常控制流执行清理。一个组件清理抛错后仍尝试其余组件，保留原始启动错误和各清理错误；shutdown 抛错记录 cleanup_error，保留初始化归属，只有确认没有执行中调用后才由原 owner 重试释放。未完成资源不虚报成功。

GPU worker 的退出等待预算为 2 秒；它只限制调用方等待，不会取消原生 NVML 调用。超时记录 cleanup_pending，不跨线程释放仍在使用的 NVML 会话；旧 worker 未退出或旧会话未释放时不启动第二个 GPU worker。其他组件照常清理；若随后重新开始非 GPU 采集，GPU 报 error 并诊断 cleanup_pending/cleanup_error，不自动重试创建 worker。旧 owner 最终释放时不能操作新生命周期。目标驱动上出现此限制则阻止候选发布并返回审查，不临时引入服务进程或复杂容错架构。

| 入口 | 本轮输出范围和无首帧行为 |
| --- | --- |
| /api/status 与 tegrastats_update | 完整有效源样本携带封存的 platform、gpu 和 telemetry_meta；同 seq 封存内容一致，响应 timestamp 和外层年龄可不同 |
| /api/cpu、/api/memory、/api/temperature、/api/power | 保留当前字段投影和返回码，既有 telemetry_meta 加源周期；不自动扩展为完整 GPU/平台接口 |
| /api/health | 保留服务存活及既有 telemetry_meta 含义，不能把 healthy 当作 GPU 可用或包身份验证；可加独立 package_version、进程身份和 capabilities 诊断；诊断不进入封存测量对象，不查询 NVML |
| 尚无有效源行 | get_current_status 仍为空、主数据 API 仍返回 503、seq=0/fresh=false，不发送主样本；先完成平台识别或 GPU 读取也不能造首帧 |

旧主板客户端兼容验收限定为既有有效 TJ 和报文接收行为；旧解析器不理解新 GPU 可用性，缺字段后的零初始化值不能称为 Thor 实测 0%。分别验证最终 Socket.IO 包封装、字节数和现有 4 KB/8 KB 接收/分片边界，保留正常报文可读性及已知越界行为证据；不能以 Python JSON 可解析或旧候选大小代替新报文验证。本轮不改天山 OS，也不跨会话修改对方生产代码。

**对应本次八项审查的共同验收矩阵**

| 编号 | 必须覆盖的交叉用例 | 通过标准 |
| --- | --- | --- |
| T1 新旧判别 | 旧 gpu={}、旧 gr3d_freq=0；新版本 1、bool/float 版本、未知版本、缺字段；新错误与旧合法字段并存 | 合法旧 0 保留，新非法/失败不走旧 fallback；有效 TJ 不被普通 GPU 问题撤下 |
| T2 平台身份 | 同 key 仅 platform 变化；同 stream 新 seq 平台/后端/源周期/GPU 周期及超时设置变化；新 stream 正常切换 | 去重前发现冲突并断段；状态恢复不误判为身份切换，不匹配模型停止；raw 不改写 |
| T3 合并顺序 | 9.9/10.0/10.1 覆盖反例、晚到、历史耗尽、错误覆盖旧成功、缓存重发、相同数值新输入 | 无未来结果和负年龄修正；有界选择正确；同 seq 全对象封存，GPU 单独更新不生主样本 |
| T4 生命周期 | CLI/server/parser/worker 实际路径：信号到达初始化/读取/关闭阶段、线程构造/启动失败、清理/shutdown 抛错及原 owner 重试、重复 stop、重启旧回调、查询超时后最终返回 | 保留信号修复和非 GPU 采集；其余清理执行、启动原错保留、成功 init/shutdown 配对；pending/error 诚实报告、无第二 worker/跨代污染 |
| T5 状态类型与年龄 | 0/非零/null、bool/字符串/越界/NaN/Infinity，四状态及恢复；独立过期、阈值相等、重复年龄推进、UTC 跳变 | 状态组合符合表格；失败不刷新旧值；封存 valid 可在消费者变为过期，TJ 保持独立有效 |
| T6 周期贯通 | CLI 的 1000/1000.0/1 与环境优先级、preview、parser.interval、进程替身命令、wire 字段、TTO 配置；旧缺周期与配置冲突 | 单位始终毫秒，1 不变 1000；实际命令与源周期一致，GPU 周期独立，未知/冲突不猜测 |
| T7 接口与客户端 | 首帧前 GPU 已就绪、HTTP/WS 并发/慢发送、分类 API/health、Orin/Thor 最大代表性报文、4 KB/8 KB 封装与分片、旧 TJ 解析 | 无首帧造数；只比较封存字段一致性；TJ 合同保持，大小和边界有新候选证据，不宣称旧客户端已支持新 GPU |
| T8 包与发布身份 | 服务非 editable wheel；Orin 无 NVML 依赖、Thor 可选绑定替身；版本一致、有效 ExecStart/解释器、旧 override/旧 PID、依赖及源码 SHA；TTO 消费候选 fixture | 干净包验证不依赖源码目录；安装/存活不冒充生效；发布/回退路径及证据完整，实际设备操作另行授权 |

共同矩阵为两会话必须实现的交叉验收；一端的通过记录不能替代另一端结果。目标 NVML 的 Python/绑定/驱动匹配、负载响应和原生调用退出行为仍需独立证据。

**分仓执行与交接**

WS 会话拥有 rm01OrinStatus 的唯一写入权，负责本仓代码/测试/服务依赖/候选/说明/证据；TTO 会话只拥有 TTO 的代码、测试、配置升级、候选和证据。此前跨仓改动作为交接起点保留、核对并接续，不回退重做，不同时写相同仓库。两边不替对方构建或宣称验收通过。

最终稿第4节是本次新增接口的唯一权威，前两份方案共同段必须与之逐字一致；若需调整，先说明证据、同步三份方案和同版fixture再继续。服务提供 Orin/Thor/unknown、错误/过期/旧格式 fixture、源码/包身份及 T1–T8 producer 结果；TTO 独立验证 consumer、Engine/Journal及原计划模型/报告链路。固定fixture目录及SHA后消费，不能在对方正在改写的目录构建交付包。各自交付后分别停止，交独立复审；不连接设备、不部署、不提交/推送/创建PR。
