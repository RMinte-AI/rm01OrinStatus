# 本轮失败与执行记录

- 上轮临时Python环境已不存在，首次调用没有执行任何测试。重新建立独立Python3.12环境，按仓库requirements固定依赖安装；未修改全局环境。
- first-test.txt：首批97项通过。test.txt：补别名/关闭异常等覆盖后108项通过。
- failed-repeat-fixture.txt、failed-install-fixture.txt：新增重复调用用例未导入test_data定义的collector夹具，108项通过、1项设置错误。补导入原夹具，未放宽断言或更改生产实现来消除失败。
- test-109-stage.txt：109项阶段通过；之后补预览不得构造服务及来源精确显示，test-final.txt与installed-replay.txt为最终110项通过。
- 原39条警告均为既有UTC时间戳API弃用，未把它们说成行为失败或设备通过。
- F1/F2修复前反例见results/F1_F2_before.json：真实Python输出管道未关闭；环境3000而旧CLI命令1000。反例子进程由测试自己创建/回收。

原先所有失败仍在工作树evidence/ws-data与历史交付备份中；最小包通过上轮ZIP指纹引用，不再嵌套完整旧包。当前身份只看identity-final.json和installed-results/wheel_identity.json。
