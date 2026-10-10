"""
Command line interface for Tegrastats API.
"""

import logging
import signal
import sys
from typing import Optional

import click

from .config import Config, normalize_tegrastats_interval
from .server import TegrastatsServer


# Global server instance for signal handling
_server_instance: Optional[TegrastatsServer] = None
_exit_requested = False


def signal_handler(signum, frame):
    """Request exit; never acquire cleanup locks from a signal handler."""
    global _exit_requested
    _exit_requested = True
    if _server_instance and _server_instance.request_shutdown():
        # Wake the blocking server loop. Its finally owns normal cleanup.
        raise SystemExit(0)


def _interval_ms(ctx, param, value):
    if value is None:
        return None
    try:
        return normalize_tegrastats_interval(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param)


@click.group()
@click.version_option()
def cli():
    """Tegrastats API - HTTP API for NVIDIA Tegra system monitoring."""
    pass


def _config_options(function):
    options = [
        click.option('--host', '-h', default=None, help='绑定IP地址'),
        click.option('--port', '-p', type=int, default=None, help='绑定端口'),
        click.option('--debug/--no-debug', default=None, help='显式启用/关闭调试；同时提供时最后一个生效'),
        click.option('--log-level', default=None, type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR'], case_sensitive=False), help='日志级别'),
        click.option('--max-connections', type=int, default=None, help='最大WebSocket连接数'),
        click.option('--update-interval', type=float, default=None, help='推送间隔(有限正数秒)'),
        click.option('--tegrastats-interval', type=str, callback=_interval_ms, default=None, help='采集间隔(正整数毫秒；1000.0等整数等值可接受)'),
    ]
    for option in reversed(options):
        function = option(function)
    return function


def _resolve_config(options):
    try:
        return Config.resolve({key: value for key, value in options.items() if value is not None})
    except ValueError as error:
        raise click.ClickException(str(error))


def _show_config(config):
    for name, label, unit in (("host", "监听主机", ""), ("port", "监听端口", ""),
                             ("tegrastats_interval", "采集间隔", " ms"),
                             ("update_interval", "推送间隔", " s"),
                             ("debug", "debug", ""), ("max_connections", "最大连接数", ""),
                             ("log_level", "日志级别", ""),
                             ("allow_unsafe_werkzeug", "allow_unsafe_werkzeug", "")):
        click.echo("%s：%s%s（来源：%s）" % (label, getattr(config, name), unit, config.sources[name]))
    click.echo("数据过期界限：%s ms" % config.freshness_timeout_ms)
    click.echo("CORS：已配置，传入CORS/Socket.IO（来源：%s）" % config.sources['cors_origins'])
    click.echo("文件日志：未接线，不代表正在写文件（来源：%s）" % config.sources['log_file'])


@cli.command()
@_config_options
def run(**options):
    """启动Tegrastats API服务器。"""
    global _server_instance, _exit_requested
    _server_instance = None
    _exit_requested = False
    config = _resolve_config(options)
    click.echo("最终启动配置：")
    _show_config(config)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logging.getLogger().setLevel(config.log_level)
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and run server
    try:
        _server_instance = TegrastatsServer(config)
        if _exit_requested:
            _server_instance.request_shutdown()
        _server_instance.run()
    except Exception as e:
        click.echo(f"服务器启动失败: {e}", err=True)
        sys.exit(1)
    finally:
        if _server_instance:
            _server_instance.stop()
        _server_instance = None


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
@_config_options
def config(**options):
    """预览本次环境与参数对应的启动配置；未查询运行中的服务。"""
    resolved = _resolve_config(options)
    click.echo("本次环境与参数对应的启动配置；未查询运行中的服务：")
    _show_config(resolved)


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()
