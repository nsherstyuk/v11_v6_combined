"""
GatewayManager — Manages IBKR Gateway lifecycle via IBC + Watchdog.

Hybrid approach:
    - Uses IBC (IB Controller) to automate Gateway login/re-auth
    - Uses ib_insync's Watchdog to monitor Gateway health and auto-restart
    - Integrates with V11's start_v11.bat wrapper for full 24/5 operation

Setup requirements:
    1. Install IBC: download from https://github.com/IbcAlpha/IBC/releases
       Extract to C:\\IBC (default path)
    2. Configure C:\\IBC\\config.ini with IBKR credentials:
       IbLoginId=YOUR_USERNAME
       IbPassword=YOUR_PASSWORD
       TradingMode=paper
       AcceptIncomingConnectionAction=accept
    3. Enable Gateway auto-restart: Configure > Lock and Exit > Auto Restart at 05:00
    4. Create Windows Scheduled Task to run StartGateway.bat at system boot

Usage:
    # Standalone: just keep Gateway alive
    python -m v11.live.gateway_manager

    # Integrated: GatewayManager starts before V11 connects
    # (start_v11.bat handles this)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Project root
ROOT = Path(__file__).resolve().parents[2]

# Default IBC paths on Windows
IBC_DEFAULT_DIR = Path(r"C:\IBC")
IBC_CONFIG_DIR = Path(os.environ.get("USERPROFILE", "")) / "Documents" / "IBC"
IBC_CONFIG_FILE = IBC_CONFIG_DIR / "config.ini"

# Default Gateway paths on Windows
GATEWAY_DEFAULT_DIR = Path(r"C:\Jts")


class GatewayManager:
    """Manages IBKR Gateway process lifecycle using IBC for auto-login.

    Responsibilities:
        1. Start Gateway via IBC (handles login dialog, 2FA, auto-restart)
        2. Monitor Gateway health (port connectivity check)
        3. Restart Gateway if it crashes or becomes unresponsive
        4. Coordinate with V11's reconnection logic

    This class does NOT manage the ib_insync IB connection — that's
    IBKRConnection's job. This class manages the Gateway PROCESS itself.
    """

    def __init__(
        self,
        ibc_dir: Path = IBC_DEFAULT_DIR,
        gateway_dir: Path = GATEWAY_DEFAULT_DIR,
        config_path: Path = IBC_CONFIG_FILE,
        port: int = 4002,
        trading_mode: str = "paper",
        gateway_version: int = 0,  # 0 = auto-detect latest
        log: Optional[logging.Logger] = None,
    ):
        self._ibc_dir = Path(ibc_dir)
        self._gateway_dir = Path(gateway_dir)
        self._config_path = Path(config_path)
        self._port = port
        self._trading_mode = trading_mode
        self._gateway_version = gateway_version
        self._log = log or logging.getLogger("gateway_manager")
        self._ibc_proc: Optional[subprocess.Popen] = None

    @property
    def ibc_installed(self) -> bool:
        """Check if IBC is installed at the configured directory."""
        return (self._ibc_dir / "StartGateway.bat").exists()

    @property
    def config_exists(self) -> bool:
        """Check if IBC config.ini exists with credentials."""
        return self._config_path.exists()

    @property
    def config_has_credentials(self) -> bool:
        """Check if config.ini has IbLoginId and IbPassword set (non-demo)."""
        if not self.config_exists:
            return False
        try:
            text = self._config_path.read_text()
            login_id = None
            password = None
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if stripped.startswith("IbLoginId="):
                    login_id = stripped[len("IbLoginId="):].strip()
                elif stripped.startswith("IbPassword="):
                    password = stripped[len("IbPassword="):].strip()
            if not login_id or not password:
                return False
            # Reject IBC demo/default credentials
            if login_id.lower() in ('edemo', 'demo') or password.lower() in ('demouser', 'demo'):
                self._log.warning("IBC config has demo credentials — replace with real IBKR credentials")
                return False
            return True
        except Exception:
            return False

    def check_gateway_health(self) -> bool:
        """Check if Gateway API is responding on the configured port.

        Performs a real IB API handshake — not just a TCP socket check.
        A bare socket check can pass even when the API layer is inactive
        (e.g. blocked by an undismissed dialog, or API not enabled).
        """
        import socket
        import struct
        try:
            sock = socket.create_connection(("127.0.0.1", self._port), timeout=5)
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False
        try:
            # Send IB API handshake: 'API\0' + length-prefixed version range
            handshake = b'API\x00'
            version_range = b'v100..176'
            msg = struct.pack('>I', len(version_range)) + version_range
            sock.sendall(handshake + msg)
            sock.settimeout(5)
            data = sock.recv(4096)
            return len(data) > 0
        except (socket.timeout, OSError):
            return False
        finally:
            sock.close()

    def start_gateway_via_ibc(self) -> bool:
        """Start IBKR Gateway via IBC's StartGateway.bat script.

        IBC handles:
            - Filling in the login dialog with username/password
            - Accepting the 2FA prompt on IBKR mobile app
            - Daily auto-restart (if configured in Gateway settings)
            - Weekly re-authentication after Sunday credential expiry

        Returns True if IBC process was started successfully.
        """
        start_script = self._ibc_dir / "StartGateway.bat"
        if not start_script.exists():
            self._log.error(
                f"IBC start script not found: {start_script}. "
                f"Install IBC from https://github.com/IbcAlpha/IBC/releases")
            return False

        if not self.config_has_credentials:
            self._log.error(
                f"IBC config missing credentials: {self._config_path}. "
                f"Set IbLoginId and IbPassword in config.ini")
            return False

        # Build command line for IBC
        cmd = [
            str(start_script),
            f"/Gateway:{self._gateway_version}" if self._gateway_version else "/Gateway",
            f"/Mode:{self._trading_mode}",
            f"/Inline",  # Required for Task Scheduler integration
        ]
        # Remove empty args
        cmd = [c for c in cmd if c]

        self._log.info(f"Starting Gateway via IBC: {' '.join(cmd)}")

        try:
            self._ibc_proc = subprocess.Popen(
                cmd,
                cwd=str(self._ibc_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32" else 0,
            )
            self._log.info(f"IBC process started (PID={self._ibc_proc.pid})")
            return True
        except Exception as e:
            self._log.error(f"Failed to start IBC: {e}")
            return False

    def send_ibc_command(self, command: str, ibc_port: int = 7462) -> bool:
        """Send a command to IBC's command server.

        Requires CommandServerPort to be set in IBC config.ini.
        Common commands: ENABLEAPI, RECONNECTDATA, RECONNECTACCOUNT, STOP.
        """
        import socket
        try:
            with socket.create_connection(("127.0.0.1", ibc_port), timeout=5) as sock:
                sock.sendall((command + "\r\n").encode())
                sock.settimeout(3)
                try:
                    resp = sock.recv(1024).decode(errors="replace")
                    self._log.info(f"IBC command '{command}': {resp.strip()}")
                except socket.timeout:
                    pass
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            self._log.warning(
                f"IBC command server not available on port {ibc_port} "
                f"— set CommandServerPort={ibc_port} in IBC config.ini")
            return False

    def reconnect_data(self) -> bool:
        """Ask IBC to reconnect the Gateway's data connection."""
        self._log.info("Sending RECONNECTDATA command to IBC...")
        return self.send_ibc_command("RECONNECTDATA")

    def stop_gateway(self) -> None:
        """Stop the IBC/Gateway process."""
        if self._ibc_proc and self._ibc_proc.poll() is None:
            self._log.info("Stopping IBC/Gateway process...")
            try:
                if sys.platform == "win32":
                    subprocess.call(
                        ["taskkill", "/F", "/T", "/PID", str(self._ibc_proc.pid)])
                else:
                    self._ibc_proc.terminate()
                    self._ibc_proc.wait(timeout=10)
            except Exception as e:
                self._log.error(f"Failed to stop IBC: {e}")
            self._ibc_proc = None

    def _check_tcp_only(self) -> bool:
        """Check if TCP socket is open (Gateway process running, API may not be ready)."""
        import socket
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=5):
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False

    def wait_for_gateway(self, timeout: float = 120) -> bool:
        """Wait for Gateway API to become available on the configured port.

        First waits for the TCP socket, then verifies the IB API handshake.
        If TCP is open but API doesn't respond after 60s, tries RECONNECTDATA.

        Args:
            timeout: Max seconds to wait for Gateway to respond.

        Returns True if Gateway is ready, False if timeout.
        """
        self._log.info(f"Waiting for Gateway on port {self._port} (timeout={timeout}s)...")
        start = time.time()
        reconnect_sent = False
        while time.time() - start < timeout:
            if self.check_gateway_health():
                elapsed = time.time() - start
                self._log.info(
                    f"Gateway is ready on port {self._port} ({elapsed:.0f}s)")
                return True
            # TCP open but API not responding after 60s — try reconnecting data
            elapsed = time.time() - start
            if not reconnect_sent and elapsed > 60 and self._check_tcp_only():
                self._log.warning(
                    "Gateway TCP port is open but API not responding after 60s "
                    "— sending RECONNECTDATA command to IBC")
                self.reconnect_data()
                reconnect_sent = True
            time.sleep(5)

        self._log.error(
            f"Gateway API failed to respond on port {self._port} "
            f"within {timeout}s")
        return False

    def _kill_stale_gateways(self) -> None:
        """Kill any existing Gateway java processes before starting a new one.

        Prevents multiple Gateway instances fighting for the same port.
        """
        if sys.platform != "win32":
            return
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | "
                 "Where-Object { $_.CommandLine -like '*ibcalpha.ibc*' -or "
                 "$_.CommandLine -like '*ibgateway*' } | "
                 "Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True, timeout=10)
            for line in result.stdout.strip().splitlines():
                pid = line.strip()
                if pid.isdigit():
                    self._log.info(f"Killing stale Gateway process PID={pid}")
                    subprocess.call(["taskkill", "/F", "/T", "/PID", pid],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            time.sleep(3)  # let ports release
        except Exception as e:
            self._log.warning(f"Stale process cleanup failed: {e}")

    def ensure_gateway_running(self, restart_timeout: float = 120) -> bool:
        """Ensure Gateway is running. Start via IBC if not.

        This is the main entry point for V11 integration:
            1. Check if Gateway is accepting connections
            2. If not, kill stale processes and start via IBC
            3. Wait for Gateway to become available
            4. Return True if Gateway is ready

        Args:
            restart_timeout: Max seconds to wait after starting IBC.

        Returns True if Gateway is ready for connections.
        """
        if self.check_gateway_health():
            return True

        self._log.warning("Gateway not responding — attempting restart via IBC")

        if not self.ibc_installed:
            self._log.error(
                "IBC not installed. Cannot auto-restart Gateway. "
                "Install IBC from https://github.com/IbcAlpha/IBC/releases "
                "and configure config.ini with your IBKR credentials.")
            return False

        # Kill any stale Gateway processes before starting fresh
        self._kill_stale_gateways()

        if not self.start_gateway_via_ibc():
            return False

        return self.wait_for_gateway(timeout=restart_timeout)

    def setup_guide(self) -> str:
        """Return setup instructions for IBC installation."""
        return f"""
+==============================================================+
|           IBC Setup Guide for V11 Auto-Reconnect            |
+==============================================================+
|                                                              |
|  1. Download IBC:                                            |
|     https://github.com/IbcAlpha/IBC/releases/latest          |
|                                                              |
|  2. Extract to C:\\IBC (right-click ZIP > Properties >        |
|     Unblock > OK, then extract)                              |
|                                                              |
|  3. Create config.ini at:                                    |
|     {str(self._config_path):<47} |
|                                                              |
|     With contents:                                           |
|     IbLoginId=YOUR_IBKR_USERNAME                             |
|     IbPassword=YOUR_IBKR_PASSWORD                            |
|     TradingMode=paper                                        |
|     AcceptIncomingConnectionAction=accept                    |
|     AutoRestartTime=05:00                                    |
|     ClosedownAt=Friday 17:00 ET                              |
|                                                              |
|  4. Enable Gateway auto-restart:                             |
|     Gateway > Configure > Lock and Exit >                    |
|     Auto Restart at 05:00 AM ET                              |
|                                                              |
|  5. Create Windows Scheduled Task:                           |
|     Action: Start a program                                   |
|     Program: C:\\IBC\\StartGateway.bat                         |
|     Arguments: /Gateway /Mode:paper /Inline                  |
|     Trigger: At system startup                               |
|     Settings: Run only when user is logged on                |
|     Settings: Stop task if runs > 5 days                     |
|                                                              |
|  6. Test: Restart computer, verify Gateway auto-starts       |
|                                                              |
|  Current status:                                             |
|    IBC installed: {str(self.ibc_installed):<39} |
|    Config exists: {str(self.config_exists):<39} |
|    Has credentials: {str(self.config_has_credentials):<37} |
|                                                              |
+==============================================================+
"""


def main():
    """Standalone Gateway health monitor.

    Modes:
        No args   — Run as persistent monitor, keeping Gateway alive
        --check   — One-shot health check, exit 0=healthy, 1=down
        --setup   — Print IBC setup guide
    """
    import argparse
    parser = argparse.ArgumentParser(description="IBKR Gateway Manager")
    parser.add_argument("--check", action="store_true",
                        help="One-shot health check (exit 0=healthy, 1=down)")
    parser.add_argument("--setup", action="store_true",
                        help="Print IBC setup guide")
    parser.add_argument("--port", type=int, default=4002,
                        help="Gateway port (default: 4002)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("gateway_manager")

    gm = GatewayManager(port=args.port, log=log)

    if args.setup:
        print(gm.setup_guide())
        sys.exit(0)

    if args.check:
        if gm.check_gateway_health():
            log.info(f"Gateway is healthy on port {args.port}")
            sys.exit(0)
        else:
            log.error(f"Gateway is NOT responding on port {args.port}")
            sys.exit(1)

    # Persistent monitor mode — single instance only
    lock_file = ROOT / "v11" / "live" / ".gateway_manager.lock"
    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text().strip())
            import ctypes
            from ctypes import wintypes, byref
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, old_pid)
            if handle:
                exit_code = wintypes.DWORD()
                kernel32.GetExitCodeProcess(handle, byref(exit_code))
                kernel32.CloseHandle(handle)
                if exit_code.value == 259:  # STILL_ACTIVE
                    log.error(
                        f"Another gateway_manager is already running (PID {old_pid}). "
                        f"Delete {lock_file} if this is stale.")
                    sys.exit(1)
                else:
                    log.info(f"Stale lock file (PID {old_pid} exited with {exit_code.value}), removing")
                    lock_file.unlink(missing_ok=True)
            # If OpenProcess returns 0, PID doesn't exist — stale lock
        except (ValueError, OSError):
            pass  # stale lock file, proceed

    if not gm.ibc_installed:
        log.error("IBC not installed. Printing setup guide:")
        print(gm.setup_guide())
        sys.exit(1)

    if not gm.config_has_credentials:
        log.error("IBC config missing credentials. Printing setup guide:")
        print(gm.setup_guide())
        sys.exit(1)

    # Write lock file
    lock_file.write_text(str(os.getpid()))
    log.info("Gateway Manager starting — monitoring IBKR Gateway health")

    check_interval = 60  # check every 60 seconds
    max_restarts_per_hour = 3
    restart_timestamps: list[float] = []

    try:
        while True:
            if gm.check_gateway_health():
                # Gateway is healthy
                time.sleep(check_interval)
                continue

            # Gateway is down — try to restart
            now = time.time()
            # Rate limit restarts
            restart_timestamps = [t for t in restart_timestamps if now - t < 3600]
            if len(restart_timestamps) >= max_restarts_per_hour:
                log.critical(
                    f"Gateway restarted {max_restarts_per_hour} times in the "
                    f"last hour — giving up. Manual intervention required.")
                break

            log.warning(
                f"Gateway not responding — attempting restart "
                f"({len(restart_timestamps)}/{max_restarts_per_hour} this hour)")
            if gm.ensure_gateway_running():
                restart_timestamps.append(time.time())
                log.info("Gateway restarted successfully")
            else:
                log.error("Gateway restart failed")
                restart_timestamps.append(time.time())
                time.sleep(30)

    except KeyboardInterrupt:
        log.info("Gateway Manager stopped by user")
    finally:
        gm.stop_gateway()
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
