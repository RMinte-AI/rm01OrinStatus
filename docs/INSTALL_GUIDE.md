# Tegrastats API 安装指南

本文档提供了 Tegrastats API 的完整安装方法和使用说明。

## 📋 目录

- [系统要求](#系统要求)
- [安装方法](#安装方法)
  - [方法1: Python包安装（推荐）](#方法1-python包安装推荐)
  - [方法2: 系统服务安装](#方法2-系统服务安装)
  - [方法3: 手动安装](#方法3-手动安装)
- [使用说明](#使用说明)
- [配置选项](#配置选项)
- [故障排除](#故障排除)

## 系统要求

- **操作系统**: Ubuntu 18.04+ (推荐 Ubuntu 20.04/22.04)
- **Python**: 3.7+ (推荐 3.9+)
- **设备**: NVIDIA Jetson 系列设备 (AGX Xavier, AGX Orin, Nano, TX2等)
- **依赖**: tegrastats 命令可用

## 安装方法

### 方法1: Python包安装（推荐）

这是最简单和推荐的安装方法，支持多种环境配置。

#### 1.1 自动安装脚本

```bash
# 下载项目
git clone <your-repository-url>
cd rm01OrinStatus

# 查看所有安装选项
./install_package.sh --help

# 标准安装（创建虚拟环境）
./install_package.sh

# 开发模式安装
./install_package.sh --dev

# 使用conda环境
./install_package.sh --conda --conda-env tegrastats-api

# 安装到当前环境
./install_package.sh --no-venv

# 静默安装
./install_package.sh --quiet --skip-tests
```

#### 1.2 手动pip安装

```bash
# 创建虚拟环境（可选）
python3 -m venv tegrastats-api-env
source tegrastats-api-env/bin/activate

# 标准安装
pip install .

# 开发模式安装
pip install -e .
```

#### 1.3 Conda安装

```bash
# 创建conda环境
conda create -n tegrastats-api python=3.9 -y
conda activate tegrastats-api

# 安装包
pip install -e .
```

### 方法2: 系统服务安装

适用于需要后台运行和开机自启动的场景。

```bash
# 使用默认配置安装系统服务
sudo ./install.sh

# 自定义配置安装
sudo ./install.sh --host 0.0.0.0 --port 58090

# 服务管理
sudo systemctl start tegrastats-api
sudo systemctl status tegrastats-api
sudo systemctl enable tegrastats-api

# 使用服务控制脚本
./service_control.sh start
./service_control.sh status
./service_control.sh logs
```

### 方法3: 手动安装

```bash
# 安装依赖
pip install -r requirements.txt

# 直接运行（开发测试用）
python app.py
```

## 使用说明

### CLI命令行使用

安装完成后，可以使用以下命令：

```bash
# 启动服务器（默认配置）
tegrastats-api run

# 自定义配置启动
tegrastats-api run --host 0.0.0.0 --port 8080 --debug

# 查看当前配置
tegrastats-api config

# 测试API连接
tegrastats-api test --host localhost --port 58090

# WebSocket实时监控
tegrastats-api monitor --host localhost --port 58090 --duration 30

# 查看帮助
tegrastats-api --help
tegrastats-api run --help
```

### Python库使用

```python
from tegrastats_api import TegrastatsServer, Config, TegrastatsParser

# 基本使用
server = TegrastatsServer()
server.run()

# 自定义配置
config = Config(
    host='0.0.0.0',
    port=8080,
    debug=True,
    max_connections=20,
    update_interval=0.5
)
server = TegrastatsServer(config)
server.run()

# 上下文管理器
with TegrastatsServer(config) as server:
    # 服务器自动启动和停止
    import time
    time.sleep(10)

# 仅使用解析器
parser = TegrastatsParser()
with parser:
    status = parser.get_current_status()
    print(status)
```

### API访问

服务器启动后，可以通过以下方式访问：

```bash
# REST API
curl http://localhost:58090/api/health
curl http://localhost:58090/api/status
curl http://localhost:58090/api/cpu
curl http://localhost:58090/api/memory
curl http://localhost:58090/api/temperature
curl http://localhost:58090/api/power

# WebSocket连接
ws://localhost:58090/socket.io/
```

## 配置选项

### 环境变量配置

```bash
export TEGRASTATS_API_HOST=0.0.0.0
export TEGRASTATS_API_PORT=58090
export TEGRASTATS_API_DEBUG=false
export TEGRASTATS_API_LOG_LEVEL=INFO
export TEGRASTATS_API_MAX_CONNECTIONS=10
export TEGRASTATS_API_UPDATE_INTERVAL=1.0
export TEGRASTATS_API_TEGRASTATS_INTERVAL=1.0
```

### 配置文件

可以通过 `Config` 类进行配置：

```python
from tegrastats_api import Config

config = Config(
    host='127.0.0.1',           # 绑定IP
    port=58090,                 # 绑定端口
    debug=False,                # 调试模式
    log_level='INFO',           # 日志级别
    max_connections=10,         # 最大WebSocket连接数
    update_interval=1.0,        # 数据更新间隔(秒)
    tegrastats_interval=1000,   # tegrastats采样间隔(毫秒；默认1000)
    cors_origins='*'            # CORS允许的源
)
```

## 故障排除

### 常见问题

#### 1. 命令找不到
```bash
# 错误: tegrastats-api: command not found
# 解决: 确保正确激活了安装环境
source your-venv/bin/activate  # 虚拟环境
conda activate your-env         # conda环境
```

#### 2. tegrastats命令不可用
```bash
# 错误: tegrastats command not found
# 解决: 确保在Jetson设备上运行，或安装tegrastats工具
sudo apt update
sudo apt install nvidia-jetpack
```

#### 3. 端口占用
```bash
# 错误: Address already in use
# 解决: 更换端口或停止占用端口的程序
tegrastats-api run --port 8080
```

#### 4. 权限错误
```bash
# 错误: Permission denied
# 解决: 检查文件权限或使用sudo（仅限系统服务安装）
chmod +x install_package.sh
```

### 调试模式

启用调试模式获取更多信息：

```bash
# CLI调试
tegrastats-api run --debug --log-level DEBUG

# Python调试
import logging
logging.basicConfig(level=logging.DEBUG)
```

### 日志查看

```bash
# 系统服务日志
sudo journalctl -u tegrastats-api -f

# 服务控制脚本
./service_control.sh logs
```

## 卸载

### Python包卸载
```bash
pip uninstall tegrastats-api
```

### 系统服务卸载
```bash
./uninstall.sh
```

### 清理环境
```bash
# 删除虚拟环境
rm -rf your-venv-name

# 删除conda环境
conda env remove -n your-env-name
```

## 支持

如遇问题，请：

1. 查看本文档的故障排除部分
2. 检查日志输出
3. 提交Issue到项目仓库
4. 联系维护团队

---

**注意**: 本软件需要在NVIDIA Jetson设备上运行以获得完整功能。在其他设备上可以安装和运行，但无法获取真实的系统监控数据。

CLI启动和config预览现在读取项目既有环境变量，显式CLI选项优先于环境。采集间隔是毫秒，推送间隔是秒；不要将采集数值乘1000。--no-debug可覆盖环境true。配置及手动部署前核对规则见WS_DATA_V1.md及交付收尾报告。
