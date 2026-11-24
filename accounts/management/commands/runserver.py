import sys
import socket
import datetime
from django.conf import settings
from django.apps import apps

try:
    from daphne.management.commands.runserver import Command as DaphneRunserverCommand
    from daphne import __version__ as daphne_version
    from daphne.endpoints import build_endpoint_description_strings
    from daphne.server import Server
    from channels.routing import get_default_application
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
    HAS_DAPHNE = True
except ImportError:
    from django.contrib.staticfiles.management.commands.runserver import Command as StaticfilesRunserverCommand
    HAS_DAPHNE = False

BaseRunserver = DaphneRunserverCommand if HAS_DAPHNE else StaticfilesRunserverCommand


class Command(BaseRunserver):
    """
    Custom runserver command that automatically binds to 0.0.0.0:8000 (all network interfaces).
    This ensures that physical mobile devices, smartphones, and tablets connected to the local
    Wi-Fi network can seamlessly access the restaurant portal, POS, and customer QR code menus
    without having to pass manual CLI flags or ever having connections refused.

    CRITICAL BROWSER FIX:
    0.0.0.0 is an INADDR_ANY socket binding address, NOT a valid HTTP destination.
    Browsers (especially Chrome on Windows) fail with ERR_ADDRESS_INVALID if navigated to http://0.0.0.0:8000/.
    This command binds to 0.0.0.0 for external/Wi-Fi access, but advertises and logs
    http://127.0.0.1:8000/ for local desktop access and IDE port forwarders.
    """
    default_addr = '0.0.0.0'
    default_port = '8000'

    def inner_run(self, *args, **options):
        # Determine machine's actual LAN IP for mobile QR access
        lan_ip = '127.0.0.1'
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                lan_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                lan_ip = '127.0.0.1'

        # Always bind to 0.0.0.0 to listen on all interfaces, while advertising 127.0.0.1 for browser navigation
        bind_host = '0.0.0.0' if self.addr in ('0.0.0.0', '') else self.addr
        display_host = '127.0.0.1' if self.addr in ('0.0.0.0', '') else self.addr

        if not HAS_DAPHNE or not options.get("use_asgi", True):
            self.addr = bind_host
            return super().inner_run(*args, **options)

        # Run system checks and migration notices
        self.stdout.write("Performing system checks...\n\n")
        self.check(display_num_errors=True)
        self.check_migrations()

        quit_command = "CTRL-BREAK" if sys.platform == "win32" else "CONTROL-C"
        now = datetime.datetime.now().strftime("%B %d, %Y - %X")

        # Announce desktop-friendly 127.0.0.1 URL so IDE port forwarders and terminal link scanners
        # open a valid URL (preventing Chrome ERR_ADDRESS_INVALID on 0.0.0.0)
        self.stdout.write(
            f"{now}\n"
            f"Django version {self.get_version()}, using settings {settings.SETTINGS_MODULE!r}\n"
            f"Starting ASGI/Daphne version {daphne_version} development server at http://{display_host}:{self.port}/\n"
            f"  > Desktop Local Access: http://{display_host}:{self.port}/\n"
            f"  > Mobile Wi-Fi Access:  http://{lan_ip}:{self.port}/\n"
            f"Listening on all network interfaces ({bind_host}:{self.port}) for Wi-Fi devices.\n"
            f"Quit the server with {quit_command}."
        )

        # Ensure port is clean and free of orphaned background processes from previous sessions
        if sys.platform == "win32":
            try:
                import subprocess
                import os
                curr_pid = str(os.getpid())
                output = subprocess.check_output(
                    f'netstat -aon | findstr ":{self.port}" | findstr "LISTENING"',
                    shell=True,
                    stderr=subprocess.DEVNULL
                ).decode('utf-8', errors='ignore')
                for line in output.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        proc_pid = parts[-1]
                        if proc_pid.isdigit() and proc_pid != curr_pid:
                            subprocess.run(f'taskkill /F /PID {proc_pid}', shell=True, capture_output=True)
            except Exception:
                pass

        endpoints = build_endpoint_description_strings(host=bind_host, port=self.port)
        try:
            self.server_cls(
                application=self.get_application(options),
                endpoints=endpoints,
                signal_handlers=not options["use_reloader"],
                action_logger=self.log_action,
                http_timeout=getattr(self, "http_timeout", None),
                root_path=getattr(settings, "FORCE_SCRIPT_NAME", "") or "",
                websocket_handshake_timeout=getattr(self, "websocket_handshake_timeout", 5),
                websocket_max_message_size=getattr(self, "websocket_max_message_size", 1024 * 1024),
                websocket_max_frame_size=getattr(self, "websocket_max_frame_size", 1024 * 1024),
            ).run()
        except KeyboardInterrupt:
            shutdown_message = options.get("shutdown_message", "")
            if shutdown_message:
                self.stdout.write(shutdown_message)
            return
