"""
Command line interface for Tegrastats API.
"""

import logging
import signal
import sys
from typing import Optional

import click

from .config import Config
from .server import TegrastatsServer


# Global server instance for signal handling
_server_instance: Optional[TegrastatsServer] = None


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    if _server_instance:
        print("\n正在关闭服务器...")
        _server_instance.stop()
    sys.exit(0)


@click.group()
@click.version_option()
def cli():
    """Tegrastats API - HTTP API for NVIDIA Tegra system monitoring."""
    pass


@cli.command()
@click.option('--host', '-h', default=None, help='绑定IP地址')
@click.option('--port', '-p', type=int, default=None, help='绑定端口')
@click.option('--debug', is_flag=True, default=False, help='启用调试模式')
@click.option('--log-level', default=None, 
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR'], case_sensitive=False),
              help='日志级别')
@click.option('--max-connections', type=int, default=None, help='最大WebSocket连接数')
@click.option('--update-interval', type=float, default=None, help='数据更新间隔(秒)')
@click.option('--tegrastats-interval', type=float, default=None, help='Tegrastats采样间隔(秒)')
def run(host, port, debug, log_level, max_connections, update_interval, tegrastats_interval):
    """启动Tegrastats API服务器。"""
    global _server_instance
    
    # Create configuration
    config = Config()
    
    # Override config with command line arguments
    if host is not None:
        config.host = host
    if port is not None:
        config.port = port
    if debug:
        config.debug = debug
    if log_level is not None:
        config.log_level = log_level.upper()
    if max_connections is not None:
        config.max_connections = max_connections
    if update_interval is not None:
        config.update_interval = update_interval
    if tegrastats_interval is not None:
        config.tegrastats_interval = tegrastats_interval
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and run server
    try:
        _server_instance = TegrastatsServer(config)
        _server_instance.run()
    except Exception as e:
        click.echo(f"服务器启动失败: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--host', '-h', default='localhost', help='服务器地址')
@click.option('--port', '-p', type=int, default=58090, help='服务器端口')
@click.option('--endpoint', '-e', default='/api/status', 
              help='API端点 (status, cpu, memory, temperature, power)')
def test(host, port, endpoint):
    """测试Tegrastats API连接。"""
    import requests
    import json
    
    try:
        url = f"http://{host}:{port}/api/{endpoint.lstrip('/api/')}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        data = response.json()
        click.echo(f"连接成功! 服务器响应:")
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        
    except requests.exceptions.ConnectionError:
        click.echo(f"无法连接到服务器 {host}:{port}", err=True)
        sys.exit(1)
    except requests.exceptions.Timeout:
        click.echo(f"连接超时", err=True)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        click.echo(f"HTTP错误: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"测试失败: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--host', '-h', default='localhost', help='服务器地址')
@click.option('--port', '-p', type=int, default=58090, help='服务器端口')
@click.option('--duration', '-d', type=int, default=10, help='监听时长(秒)')
def monitor(host, port, duration):
    """通过WebSocket监听实时数据。"""
    import websocket
    import json
    import time
    import threading
    
    messages_received = 0
    start_time = time.time()
    
    def on_message(ws, message):
        nonlocal messages_received
        messages_received += 1
        
        try:
            data = json.loads(message)
            timestamp = data.get('timestamp', 'N/A')
            
            # Display key metrics
            if 'cpu' in data:
                cpu_usage = data['cpu'].get('usage', 'N/A')
                click.echo(f"[{timestamp}] CPU使用率: {cpu_usage}%")
            
            if 'memory' in data:
                memory = data['memory']
                used = memory.get('used_mb', 'N/A')
                total = memory.get('total_mb', 'N/A')
                click.echo(f"[{timestamp}] 内存: {used}/{total} MB")
            
            if 'temperature' in data:
                temps = data['temperature']
                if temps:
                    temp_str = ", ".join([f"{k}: {v}°C" for k, v in temps.items()])
                    click.echo(f"[{timestamp}] 温度: {temp_str}")
            
        except json.JSONDecodeError:
            click.echo(f"收到无效JSON: {message}")
    
    def on_error(ws, error):
        click.echo(f"WebSocket错误: {error}", err=True)
    
    def on_close(ws, close_status_code, close_msg):
        elapsed = time.time() - start_time
        click.echo(f"\n连接关闭. 运行时间: {elapsed:.1f}s, 收到消息: {messages_received}")
    
    def on_open(ws):
        click.echo(f"连接到 ws://{host}:{port}")
        click.echo(f"监听 {duration} 秒...")
        
        # Close connection after duration
        def close_after_duration():
            time.sleep(duration)
            ws.close()
        
        threading.Thread(target=close_after_duration, daemon=True).start()
    
    try:
        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            f"ws://{host}:{port}/socket.io/?EIO=4&transport=websocket",
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        ws.run_forever()
        
    except KeyboardInterrupt:
        click.echo("\n用户中断")
    except Exception as e:
        click.echo(f"监听失败: {e}", err=True)
        sys.exit(1)


@cli.command()
def config():
    """显示当前配置。"""
    from .config import Config
    
    config = Config()
    
    click.echo("当前配置:")
    click.echo(f"  主机地址: {config.host}")
    click.echo(f"  端口: {config.port}")
    click.echo(f"  调试模式: {config.debug}")
    click.echo(f"  日志级别: {config.log_level}")
    click.echo(f"  最大连接数: {config.max_connections}")
    click.echo(f"  更新间隔: {config.update_interval}秒")
    click.echo(f"  Tegrastats间隔: {config.tegrastats_interval}秒")
    click.echo(f"  CORS源: {config.cors_origins}")


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()