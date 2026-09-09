"""SSH 自动部署 Agent 服务"""
import paramiko
import time


AGENT_SCRIPT = r'''"""数据采集 Agent - 部署在目标机器上，定时采集指标上报后端"""
import os, time, json, socket, platform, subprocess
from datetime import datetime

class MetricsCollector:
    def __init__(self, backend_url, interval=10):
        self.backend_url = backend_url.rstrip("/")
        self.interval = interval
        self.hostname = socket.gethostname()
        self.machine_id = None

    def register(self):
        import urllib.request
        try:
            data = json.dumps({"name": self.hostname, "ip": self._get_local_ip(),
                "device_type": "physical", "group_name": "auto-registered",
                "tags": f"os:{platform.system()},arch:{platform.machine()}",
                "remark": f"Auto-registered by Agent v1.0 - {platform.platform()}"}).encode()
            req = urllib.request.Request(f"{self.backend_url}/api/machines/", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=5)
            result = json.loads(resp.read())
            self.machine_id = result.get("id")
            print(f"[Agent] Registered ID={self.machine_id}")
        except:
            try:
                req = urllib.request.Request(f"{self.backend_url}/api/machines/?keyword={self._get_local_ip()}")
                resp = urllib.request.urlopen(req, timeout=5)
                machines = json.loads(resp.read())
                if machines: self.machine_id = machines[0]["id"]
            except: pass

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
        except: return "127.0.0.1"

    def _read_cpu_stats(self):
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu "):
                    vals = [int(x) for x in line.split()[1:9]]
                    return sum(vals), vals[3] + vals[4]
        return 0, 0

    def _read_disk_io(self):
        try:
            with open("/proc/diskstats") as f:
                for line in f:
                    p = line.split()
                    if p[2] in ("sda","nvme0n1","vda"): return int(p[5]), int(p[9])
        except: pass
        return 0, 0

    def collect_metrics(self):
        m = {"machine_id": self.machine_id, "timestamp": datetime.now().isoformat()}
        t1, i1 = self._read_cpu_stats()
        dr1, dw1 = self._read_disk_io()

        # network sample 1
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
        rx1 = tx1 = 0
        for line in lines:
            p = line.split(":")
            if len(p) >= 2 and p[0].strip() != "lo":
                s = p[1].split(); rx1 += int(s[0]); tx1 += int(s[8])

        time.sleep(0.5)

        # cpu sample 2
        t2, i2 = self._read_cpu_stats()
        td = t2 - t1; id = i2 - i1
        m["cpu_percent"] = round((td - id) / td * 100, 1) if td > 0 else 0
        try:
            with open("/proc/cpuinfo") as f: m["cpu_cores"] = sum(1 for l in f if l.startswith("processor"))
        except: m["cpu_cores"] = 1

        # disk io sample 2
        dr2, dw2 = self._read_disk_io()
        m["disk_read_mbps"] = round((dr2 - dr1) * 512 / 1024 / 1024 / 0.5, 2) if dr2 >= dr1 else 0
        m["disk_write_mbps"] = round((dw2 - dw1) * 512 / 1024 / 1024 / 0.5, 2) if dw2 >= dw1 else 0

        # memory
        mi = {}
        with open("/proc/meminfo") as f:
            for l in f:
                p = l.split(":")
                if len(p) >= 2: mi[p[0].strip()] = int(p[1].strip().split()[0])
        total = mi.get("MemTotal", 1); avail = mi.get("MemAvailable", 0)
        m["memory_total"] = round(total/1024/1024, 2); m["memory_used"] = round((total-avail)/1024/1024, 2)
        m["memory_percent"] = round((total-avail)/total*100, 1)
        m["swap_total"] = round(mi.get("SwapTotal",0)/1024/1024, 2)
        m["swap_used"] = round((mi.get("SwapTotal",0)-mi.get("SwapFree",0))/1024/1024, 2)

        # disk usage
        s = os.statvfs("/")
        dt = s.f_frsize * s.f_blocks; du = dt - s.f_frsize * s.f_bavail
        m["disk_total"] = round(dt/1024/1024/1024, 2); m["disk_used"] = round(du/1024/1024/1024, 2)
        m["disk_percent"] = round(du/dt*100, 1) if dt > 0 else 0

        # network sample 2
        with open("/proc/net/dev") as f: lines = f.readlines()[2:]
        rx2 = tx2 = 0
        for line in lines:
            p = line.split(":")
            if len(p) >= 2 and p[0].strip() != "lo":
                s = p[1].split(); rx2 += int(s[0]); tx2 += int(s[8])
        m["network_in_mbps"] = round((rx2-rx1)*8/1024/1024/0.5, 2) if rx2>=rx1 else 0
        m["network_out_mbps"] = round((tx2-tx1)*8/1024/1024/0.5, 2) if tx2>=tx1 else 0
        m["network_connections"] = len([d for d in os.listdir("/proc") if d.isdigit()][:100])

        with open("/proc/loadavg") as f: parts = f.read().split()
        m["load_1m"], m["load_5m"], m["load_15m"] = float(parts[0]), float(parts[1]), float(parts[2])

        with open("/proc/uptime") as f: m["uptime_seconds"] = int(float(f.read().split()[0]))
        m["process_count"] = len([d for d in os.listdir("/proc") if d.isdigit()])
        m["cpu_temp"] = 0
        for path in ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"]:
            try:
                with open(path) as f: m["cpu_temp"] = float(f.read().strip())/1000.0; break
            except: pass
        return m

    def report(self, metrics):
        import urllib.request
        try:
            data = json.dumps(metrics).encode()
            req = urllib.request.Request(f"{self.backend_url}/api/metrics/report", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except: pass

    def run(self):
        print(f"[Agent] Started - backend={self.backend_url}")
        mid = os.environ.get("MACHINE_ID", "")
        if mid:
            self.machine_id = int(mid)
            print(f"[Agent] Using specified ID={self.machine_id}")
        else:
            self.register()
        while True:
            try:
                m = self.collect_metrics()
                self.report(m)
            except Exception as e:
                print(f"[Agent] Error: {e}")
            time.sleep(self.interval)

if __name__ == "__main__":
    import os
    url = os.environ.get("BACKEND_URL", "http://localhost:8000")
    MetricsCollector(url).run()
'''


def deploy_agent(host: str, port: int, username: str, password: str,
                 backend_url: str = "http://localhost:8000",
                 machine_id: int = None) -> dict:
    """SSH 到目标机器，自动部署并启动 Agent

    Returns:
        {"success": True/False, "message": "...", "machine_id": int}
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(host, port=port, username=username, password=password, timeout=10, banner_timeout=10)

        # 1. 确定安装目录（非 root 用户可能没有 /opt 写权限）
        stdin, stdout, stderr = ssh.exec_command('mkdir -p /opt/monitor-agent 2>/dev/null && echo OK_OPT || echo NO_OPT_PERM')
        result = stdout.read().decode().strip()
        if result == 'NO_OPT_PERM':
            ssh.exec_command('mkdir -p ~/monitor-agent')
            agent_dir = '~/monitor-agent'
            log_path = '~/monitor-agent/agent.log'
        else:
            agent_dir = '/opt/monitor-agent'
            log_path = '/var/log/monitor-agent.log'
        time.sleep(0.3)

        # 2. 检查 Python3，没有则自动安装
        stdin, stdout, stderr = ssh.exec_command('which python3 2>/dev/null || echo NOT_FOUND')
        python_path = stdout.read().decode().strip()
        if python_path == 'NOT_FOUND':
            # 检测 OS 类型，自动安装 Python3
            stdin, stdout, stderr = ssh.exec_command(
                'cat /etc/os-release 2>/dev/null | grep -E "^ID=" | cut -d= -f2 | tr -d \\"\\"'
            )
            os_id = stdout.read().decode().strip().lower()
            sudo_prefix = 'sudo ' if username != 'root' else ''
            
            if os_id in ('ubuntu', 'debian'):
                install_cmd = f'{sudo_prefix}apt-get update -qq && {sudo_prefix}apt-get install -y -qq python3'
            elif os_id in ('centos', 'rhel', 'rocky', 'almalinux', 'fedora'):
                install_cmd = f'{sudo_prefix}dnf install -y python3 2>/dev/null || {sudo_prefix}yum install -y python3'
            elif os_id == 'opensuse-leap' or os_id == 'opensuse-tumbleweed':
                install_cmd = f'{sudo_prefix}zypper install -y python3'
            elif os_id == 'arch':
                install_cmd = f'{sudo_prefix}pacman -S --noconfirm python'
            else:
                install_cmd = f'{sudo_prefix}apt-get update -qq && {sudo_prefix}apt-get install -y -qq python3 2>/dev/null || {sudo_prefix}dnf install -y python3 2>/dev/null || {sudo_prefix}yum install -y python3'
            
            stdin, stdout, stderr = ssh.exec_command(install_cmd, timeout=120)
            _ = stdout.read()
            time.sleep(1)
            
            # 重新验证
            stdin, stdout, stderr = ssh.exec_command('which python3 2>/dev/null || echo NOT_FOUND')
            python_path = stdout.read().decode().strip()
            if python_path == 'NOT_FOUND':
                return {"success": False, "message": "Python3 自动安装失败，请手动安装后重试"} 

        # 3. 通过 SSH stdin 管道写入 agent 脚本（绕过 SFTP 兼容性问题）
        stdin, stdout, stderr = ssh.exec_command(f'cat > {agent_dir}/agent.py')
        stdin.write(AGENT_SCRIPT)
        stdin.channel.shutdown_write()
        _ = stdout.channel.recv_exit_status()
        time.sleep(0.5)

        # 4. 停止旧 agent
        ssh.exec_command('pkill -f agent.py 2>/dev/null')
        time.sleep(1)

        # 5. 启动 agent
        env_vars = f'BACKEND_URL={backend_url}'
        if machine_id:
            env_vars += f' MACHINE_ID={machine_id}'
        ssh.exec_command(f'{env_vars} nohup {python_path} -u {agent_dir}/agent.py > {log_path} 2>&1 &')
        time.sleep(3)

        # 6. 验证
        stdin, stdout, stderr = ssh.exec_command('ps aux | grep "agent.py" | grep -v grep | wc -l')
        count = int(stdout.read().decode().strip() or '0')

        ssh.close()

        if count > 0:
            return {"success": True, "message": f"Agent 部署成功 ({agent_dir})"}
        else:
            stdin, stdout, stderr = ssh.exec_command(f'cat {log_path} 2>/dev/null | tail -5')
            log = stdout.read().decode().strip()
            return {"success": True, "message": f"Agent 已部署，启动中。日志: {log[:200]}"}

    except paramiko.AuthenticationException:
        return {"success": False, "message": "SSH 认证失败，请检查用户名/密码"}
    except Exception as e:
        return {"success": False, "message": f"SSH 连接失败: {str(e)[:100]}"}
    finally:
        try:
            ssh.close()
        except:
            pass
