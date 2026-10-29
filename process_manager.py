import os
import sys
import time
import signal
import shutil
import zipfile
import subprocess
import threading
import importlib.metadata
from typing import Dict, Any, List, Callable, Optional, Tuple, Set
import psutil

from config import (
    WORKSPACE_DIR,
    LOG_TAIL_LINES,
    PIP_TIMEOUT_SECONDS,
    MAX_ZIP_UNCOMPRESSED_MB,
    MAX_ZIP_FILES_COUNT,
    ENTRY_POINT_CANDIDATES
)
from db_manager import db
from security_scanner import SecurityScanner


class ProcessManager:
    """
    Handles isolated user workspaces, sequential pip dependency installation,
    real-time background process execution, log streaming, resource monitoring,
    safe .zip archive extraction, and automated GitHub repository deployment.
    """

    def __init__(self):
        self.active_processes: Dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()

    def get_user_workspace(self, user_id: int) -> str:
        path = os.path.join(WORKSPACE_DIR, str(user_id))
        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "logs"), exist_ok=True)
        os.makedirs(os.path.join(path, "repos"), exist_ok=True)
        os.makedirs(os.path.join(path, "projects"), exist_ok=True)
        return path

    # ==========================================
    # PACKAGE & TERMINAL PIP HANDLER
    # ==========================================
    @staticmethod
    def get_installed_distributions() -> Set[str]:
        installed = set()
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name", "")
            if name:
                installed.add(name.lower().replace("_", "-"))
        return installed

    def check_missing_requirements(self, required_packages: List[str]) -> List[str]:
        installed = self.get_installed_distributions()
        missing = []
        for pkg in required_packages:
            clean_pkg = pkg.split("==")[0].split(">=")[0].split("<=")[0].strip().lower().replace("_", "-")
            if clean_pkg and clean_pkg not in installed:
                missing.append(pkg)
        return missing

    def install_single_package(self, user_id: int, package_spec: str) -> Tuple[bool, str]:
        """Installs a single pip package and captures real-time stdout/stderr."""
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.[],=<>!~")
        if not package_spec or not all(c in allowed_chars for c in package_spec):
            return False, f"Invalid package specification: `{package_spec}`"

        cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", package_spec]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=PIP_TIMEOUT_SECONDS
            )
            output = (proc.stdout + "\n" + proc.stderr).strip()
            if proc.returncode == 0:
                db.add_installed_package(user_id, package_spec)
                return True, output[-1500:] if len(output) > 1500 else output
            else:
                return False, output[-1500:] if len(output) > 1500 else output
        except subprocess.TimeoutExpired:
            return False, f"Installation of `{package_spec}` timed out after {PIP_TIMEOUT_SECONDS}s."
        except Exception as e:
            return False, f"Execution error: {str(e)}"

    # ==========================================
    # PROCESS HOSTING & MONITORING
    # ==========================================
    def start_file(
        self,
        file_id: str,
        on_crash_callback: Optional[Callable[[int, str, str, int, str], None]] = None
    ) -> Tuple[bool, str]:
        """Starts a hosted Python script or project in an isolated background subprocess."""
        file_record = db.get_file(file_id)
        if not file_record:
            return False, "File record not found in database."

        if self.is_running(file_id):
            return False, "Process is already running."

        file_path = file_record["file_path"]
        user_id = file_record["user_id"]
        file_name = file_record["file_name"]

        if not os.path.exists(file_path):
            return False, f"Entry file missing on disk: `{file_path}`"

        # Re-verify security before starting (whole project dir if multi-file, else single file)
        project_dir = file_record.get("project_dir")
        if project_dir and os.path.exists(project_dir):
            scan = SecurityScanner.scan_directory(project_dir)
        else:
            scan = SecurityScanner.scan_file(file_path)

        if not scan["is_safe"]:
            db.update_file_status(file_id, "BLOCKED")
            return False, "Security violation detected:\n" + "\n".join(scan["reasons"][:8])

        user_workspace = self.get_user_workspace(user_id)
        log_path = os.path.join(user_workspace, "logs", f"{file_id}.log")
        working_dir = os.path.dirname(file_path)

        # Build isolated environment variables for child process
        user_data = db.get_user(user_id) or {}
        custom_env = user_data.get("settings", {}).get("env_vars", {})

        child_env = os.environ.copy()
        child_env.pop("BOT_TOKEN", None)  # Prevent hosted scripts from reading the hoster's token
        child_env["PYTHONUNBUFFERED"] = "1"
        for k, v in custom_env.items():
            child_env[str(k)] = str(v)

        try:
            log_file = open(log_path, "w", encoding="utf-8", buffering=1)
            log_file.write(f"=== SESSION STARTED AT {time.ctime()} ===\n")
            log_file.write(f"=== ENTRY: {file_name} | WORKDIR: {working_dir} ===\n\n")

            proc = subprocess.Popen(
                [sys.executable, "-u", file_path],
                cwd=working_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=child_env,
                preexec_fn=os.setsid if os.name != "nt" else None
            )

            with self.lock:
                self.active_processes[file_id] = proc

            db.register_session(file_id, user_id, proc.pid, log_path)

            watcher = threading.Thread(
                target=self._watch_process,
                args=(file_id, user_id, file_name, proc, log_file, log_path, on_crash_callback),
                daemon=True
            )
            watcher.start()

            return True, f"Started `{file_name}` with PID `{proc.pid}`."
        except Exception as e:
            return False, f"Failed to spawn process: {str(e)}"

    def _watch_process(
        self,
        file_id: str,
        user_id: int,
        file_name: str,
        proc: subprocess.Popen,
        log_file,
        log_path: str,
        on_crash_callback: Optional[Callable]
    ):
        exit_code = proc.wait()
        try:
            log_file.write(f"\n=== PROCESS EXITED WITH CODE {exit_code} AT {time.ctime()} ===\n")
            log_file.close()
        except Exception:
            pass

        with self.lock:
            was_tracked = file_id in self.active_processes
            self.active_processes.pop(file_id, None)

        db.remove_session(file_id)

        if not was_tracked:
            db.update_file_status(file_id, "STOPPED", exit_code)
            return

        if exit_code == 0:
            db.update_file_status(file_id, "COMPLETED", exit_code)
        else:
            db.update_file_status(file_id, "CRASHED", exit_code)
            error_tail = self.read_logs(file_id, lines=25)
            if on_crash_callback:
                try:
                    on_crash_callback(user_id, file_id, file_name, exit_code, error_tail)
                except Exception:
                    pass

            user_data = db.get_user(user_id)
            if user_data and user_data.get("settings", {}).get("auto_restart_on_crash"):
                time.sleep(3)
                self.start_file(file_id, on_crash_callback)

    def stop_file(self, file_id: str) -> Tuple[bool, str]:
        with self.lock:
            proc = self.active_processes.pop(file_id, None)

        session = db.get_all_sessions().get(file_id)
        pid = proc.pid if proc else (session.get("pid") if session else None)

        if not proc and not pid:
            db.update_file_status(file_id, "STOPPED")
            db.remove_session(file_id)
            return False, "Process is not currently running."

        try:
            if os.name != "nt" and pid:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            elif proc:
                proc.terminate()
            time.sleep(0.5)
            if proc and proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            pass
        except Exception as e:
            return False, f"Error terminating PID `{pid}`: {str(e)}"

        db.remove_session(file_id)
        db.update_file_status(file_id, "STOPPED")
        return True, f"Process (PID `{pid}`) terminated cleanly."

    def stop_all_processes(self) -> int:
        with self.lock:
            file_ids = list(self.active_processes.keys())
        count = 0
        for fid in file_ids:
            ok, _ = self.stop_file(fid)
            if ok:
                count += 1
        return count

    def is_running(self, file_id: str) -> bool:
        with self.lock:
            proc = self.active_processes.get(file_id)
            return bool(proc and proc.poll() is None)

    def get_process_metrics(self, file_id: str) -> Dict[str, Any]:
        with self.lock:
            proc = self.active_processes.get(file_id)
        if not proc or proc.poll() is not None:
            return {"running": False, "pid": None, "cpu": 0.0, "ram_mb": 0.0, "uptime": "0s"}

        try:
            p = psutil.Process(proc.pid)
            cpu = p.cpu_percent(interval=0.1)
            mem_mb = p.memory_info().rss / (1024 * 1024)
            uptime_sec = int(time.time() - p.create_time())
            mins, secs = divmod(uptime_sec, 60)
            hrs, mins = divmod(mins, 60)
            return {
                "running": True,
                "pid": proc.pid,
                "cpu": round(cpu, 1),
                "ram_mb": round(mem_mb, 2),
                "uptime": f"{hrs}h {mins}m {secs}s"
            }
        except Exception:
            return {"running": False, "pid": proc.pid, "cpu": 0.0, "ram_mb": 0.0, "uptime": "N/A"}

    def read_logs(self, file_id: str, lines: int = LOG_TAIL_LINES) -> str:
        file_rec = db.get_file(file_id)
        if not file_rec:
            return "No file record found."
        user_workspace = self.get_user_workspace(file_rec["user_id"])
        log_path = os.path.join(user_workspace, "logs", f"{file_id}.log")
        if not os.path.exists(log_path):
            return "No log output generated yet."

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                tail = "".join(all_lines[-lines:])
                return tail[-3500:] if tail.strip() else "Log file is currently empty."
        except Exception as e:
            return f"Failed to read logs: {str(e)}"

    # ==========================================
    # SHARED MULTI-FILE PROJECT PREPARER
    # ==========================================
    def _prepare_project_directory(
        self,
        user_id: int,
        project_dir: str,
        project_name: str,
        progress_callback: Callable[[str], None]
    ) -> Dict[str, Any]:
        """
        Scans a multi-file project directory (from .zip or GitHub), installs requirements
        one-by-one, and detects the primary entry point plus alternative .py files.
        """
        progress_callback(f"🛡️ Scanning all files in `{project_name}` for malicious code...")
        scan_result = SecurityScanner.scan_directory(project_dir)
        if not scan_result["is_safe"]:
            shutil.rmtree(project_dir, ignore_errors=True)
            db.increment_stat("blocked_malicious_files")
            return {
                "success": False,
                "error": "Security Scanner blocked this project:\n" + "\n".join(scan_result["reasons"][:10])
            }

        # Collect requirements from requirements.txt or AST imports across the project
        req_file = os.path.join(project_dir, "requirements.txt")
        requirements = []
        if os.path.exists(req_file):
            with open(req_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if line and not line.startswith("-"):
                        requirements.append(line)
        else:
            requirements = scan_result.get("imports", [])

        # Install requirements sequentially one by one
        failed_reqs = []
        if requirements:
            for idx, req in enumerate(requirements, start=1):
                progress_callback(f"📦 Installing requirement ({idx}/{len(requirements)}): `pip install {req}`...")
                ok, out = self.install_single_package(user_id, req)
                if not ok:
                    failed_reqs.append((req, out))

        if failed_reqs:
            pkg, err_log = failed_reqs[0]
            return {
                "success": False,
                "error": f"Failed during `pip install {pkg}`:\n```text\n{err_log[-1000:]}\n```"
            }

        # Discover all Python files inside the project (up to 2 levels deep for entry selection)
        all_py_rel_paths = []
        for root, dirs, files in os.walk(project_dir):
            # Ignore hidden directories and virtual environments
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "__pycache__")]
            for f in sorted(files):
                if f.endswith(".py"):
                    rel_p = os.path.relpath(os.path.join(root, f), project_dir)
                    all_py_rel_paths.append(rel_p)

        if not all_py_rel_paths:
            shutil.rmtree(project_dir, ignore_errors=True)
            return {"success": False, "error": "No Python (`.py`) files found inside the project."}

        # Pick primary entry point
        selected_rel_entry = None
        for candidate in ENTRY_POINT_CANDIDATES:
            if candidate in all_py_rel_paths:
                selected_rel_entry = candidate
                break

        if not selected_rel_entry:
            selected_rel_entry = all_py_rel_paths[0]

        entry_point_full = os.path.join(project_dir, selected_rel_entry)

        return {
            "success": True,
            "project_name": project_name,
            "project_dir": project_dir,
            "entry_point": entry_point_full,
            "entry_name": selected_rel_entry,
            "available_entrypoints": all_py_rel_paths[:15],
            "requirements": requirements
        }

    # ==========================================
    # SAFE .ZIP PROJECT EXTRACTOR & AUTO-HOSTER
    # ==========================================
    def extract_and_prepare_zip(
        self,
        user_id: int,
        zip_file_path: str,
        original_zip_name: str,
        progress_callback: Callable[[str], None]
    ) -> Dict[str, Any]:
        """
        Validates a .zip archive against Zip Bombs and ZipSlip path traversal,
        extracts it to an isolated workspace directory, flattens single root folders,
        scans all code, installs dependencies, and identifies the entry script.
        """
        project_name = os.path.splitext(os.path.basename(original_zip_name))[0]
        user_workspace = self.get_user_workspace(user_id)
        target_dir = os.path.join(user_workspace, "projects", f"{project_name}_{int(time.time())}")
        os.makedirs(target_dir, exist_ok=True)

        progress_callback(f"📦 Validating & extracting archive `{original_zip_name}`...")
        try:
            if not zipfile.is_zipfile(zip_file_path):
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": "Uploaded file is not a valid `.zip` archive."}

            with zipfile.ZipFile(zip_file_path, "r") as zf:
                info_list = zf.infolist()

                if len(info_list) > MAX_ZIP_FILES_COUNT:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    return {
                        "success": False,
                        "error": f"Archive contains `{len(info_list)}` files (Max allowed: `{MAX_ZIP_FILES_COUNT}`)."
                    }

                total_uncompressed = sum(info.file_size for info in info_list)
                max_bytes = MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024
                if total_uncompressed > max_bytes:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    return {
                        "success": False,
                        "error": f"Uncompressed size exceeds `{MAX_ZIP_UNCOMPRESSED_MB} MB` limit (Zip Bomb protection)."
                    }

                # Safe member-by-member extraction with ZipSlip path traversal check
                abs_target_dir = os.path.abspath(target_dir)
                for member in info_list:
                    # Skip macOS metadata and __pycache__
                    if "__MACOSX" in member.filename or "__pycache__" in member.filename:
                        continue

                    dest_path = os.path.abspath(os.path.join(target_dir, member.filename))
                    if not dest_path.startswith(abs_target_dir + os.sep) and dest_path != abs_target_dir:
                        shutil.rmtree(target_dir, ignore_errors=True)
                        db.increment_stat("blocked_malicious_files")
                        return {
                            "success": False,
                            "error": f"Security Alert: Blocked path traversal (`ZipSlip`) attempt in `{member.filename}`."
                        }

                    if member.is_dir():
                        os.makedirs(dest_path, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        with zf.open(member) as src, open(dest_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)

        except Exception as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return {"success": False, "error": f"Archive extraction error: `{str(e)}`"}
        finally:
            if os.path.exists(zip_file_path):
                try:
                    os.remove(zip_file_path)
                except Exception:
                    pass

        # If the user zipped a single parent folder (e.g. my_bot/main.py), flatten into target_dir
        entries = [e for e in os.listdir(target_dir) if not e.startswith(".")]
        if len(entries) == 1:
            single_nested = os.path.join(target_dir, entries[0])
            if os.path.isdir(single_nested):
                for item in os.listdir(single_nested):
                    shutil.move(os.path.join(single_nested, item), os.path.join(target_dir, item))
                os.rmdir(single_nested)

        return self._prepare_project_directory(user_id, target_dir, project_name, progress_callback)

    # ==========================================
    # GITHUB AUTO-HOST ENGINE
    # ==========================================
    def clone_and_prepare_github(
        self,
        user_id: int,
        repo_url: str,
        progress_callback: Callable[[str], None]
    ) -> Dict[str, Any]:
        if not repo_url.startswith(("https://github.com/", "http://github.com/")):
            return {"success": False, "error": "Invalid URL. Must be a public `https://github.com/user/repo` link."}

        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        user_workspace = self.get_user_workspace(user_id)
        target_dir = os.path.join(user_workspace, "repos", f"{repo_name}_{int(time.time())}")

        progress_callback(f"⏳ Cloning repository `{repo_name}`...")
        try:
            clone_proc = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, target_dir],
                capture_output=True,
                text=True,
                timeout=90
            )
            if clone_proc.returncode != 0:
                return {"success": False, "error": f"Git clone failed:\n`{clone_proc.stderr.strip()}`"}
        except Exception as e:
            return {"success": False, "error": f"Git execution error: {str(e)}"}

        return self._prepare_project_directory(user_id, target_dir, repo_name, progress_callback)


pm = ProcessManager()
