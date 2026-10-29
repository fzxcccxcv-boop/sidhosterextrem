import json
import os
import threading
import time
from typing import Dict, Any, List, Optional
from config import DB_FILE_PATH


class DatabaseManager:
    """Thread-safe JSON Database Manager for users, files, multi-file projects, and sessions."""

    def __init__(self, db_path: str = DB_FILE_PATH):
        self.db_path = db_path
        self.lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self):
        with self.lock:
            if not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0:
                initial_schema = {
                    "users": {},
                    "files": {},
                    "sessions": {},
                    "banned_users": [],
                    "system_stats": {
                        "total_uploads": 0,
                        "total_zip_deploys": 0,
                        "total_github_deploys": 0,
                        "blocked_malicious_files": 0,
                        "total_pip_installs": 0
                    }
                }
                self._write(initial_schema)

    def _read(self) -> Dict[str, Any]:
        with self.lock:
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data.setdefault("system_stats", {})
                    data["system_stats"].setdefault("total_zip_deploys", 0)
                    return data
            except (json.JSONDecodeError, FileNotFoundError):
                self._ensure_db()
                with open(self.db_path, "r", encoding="utf-8") as f:
                    return json.load(f)

    def _write(self, data: Dict[str, Any]):
        with self.lock:
            temp_path = f"{self.db_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.db_path)

    # ==========================================
    # USER MANAGEMENT
    # ==========================================
    def register_user(self, user_id: int, username: str, first_name: str) -> Dict[str, Any]:
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid not in data["users"]:
                data["users"][uid] = {
                    "user_id": user_id,
                    "username": username or "Unknown",
                    "first_name": first_name or "User",
                    "joined_at": int(time.time()),
                    "state": "IDLE",
                    "state_meta": {},
                    "settings": {
                        "auto_restart_on_crash": False,
                        "notify_on_crash": True,
                        "auto_scan_imports": True,
                        "env_vars": {}
                    },
                    "installed_packages": []
                }
            else:
                data["users"][uid]["username"] = username or data["users"][uid].get("username", "Unknown")
                data["users"][uid]["first_name"] = first_name or data["users"][uid].get("first_name", "User")
            self._write(data)
            return data["users"][uid]

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self._read()["users"].get(str(user_id))

    def get_all_users(self) -> Dict[str, Any]:
        return self._read().get("users", {})

    def set_user_state(self, user_id: int, state: str, meta: Optional[Dict[str, Any]] = None):
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid in data["users"]:
                data["users"][uid]["state"] = state
                data["users"][uid]["state_meta"] = meta or {}
                self._write(data)

    def update_user_setting(self, user_id: int, key: str, value: Any):
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid in data["users"]:
                data["users"][uid]["settings"][key] = value
                self._write(data)

    def set_user_env_var(self, user_id: int, env_key: str, env_val: str):
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid in data["users"]:
                data["users"][uid]["settings"]["env_vars"][env_key] = env_val
                self._write(data)

    def clear_user_env_vars(self, user_id: int):
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid in data["users"]:
                data["users"][uid]["settings"]["env_vars"] = {}
                self._write(data)

    def add_installed_package(self, user_id: int, package_name: str):
        with self.lock:
            data = self._read()
            uid = str(user_id)
            if uid in data["users"]:
                pkgs = data["users"][uid].get("installed_packages", [])
                if package_name not in pkgs:
                    pkgs.append(package_name)
                    data["users"][uid]["installed_packages"] = pkgs
                data["system_stats"]["total_pip_installs"] += 1
                self._write(data)

    # ==========================================
    # BAN MANAGEMENT
    # ==========================================
    def is_banned(self, user_id: int) -> bool:
        return int(user_id) in self._read().get("banned_users", [])

    def ban_user(self, user_id: int):
        with self.lock:
            data = self._read()
            if int(user_id) not in data["banned_users"]:
                data["banned_users"].append(int(user_id))
                self._write(data)

    def unban_user(self, user_id: int):
        with self.lock:
            data = self._read()
            if int(user_id) in data["banned_users"]:
                data["banned_users"].remove(int(user_id))
                self._write(data)

    # ==========================================
    # FILE & PROJECT RECORDS MANAGEMENT
    # ==========================================
    def add_file_record(
        self,
        file_id: str,
        user_id: int,
        file_name: str,
        file_path: str,
        source_type: str = "upload",  # 'upload', 'zip', or 'github'
        requirements: Optional[List[str]] = None,
        scan_status: str = "CLEAN",
        project_dir: Optional[str] = None,
        available_entrypoints: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        with self.lock:
            data = self._read()
            record = {
                "file_id": file_id,
                "user_id": int(user_id),
                "file_name": file_name,
                "file_path": file_path,
                "project_dir": project_dir,
                "available_entrypoints": available_entrypoints or [],
                "source_type": source_type,
                "requirements": requirements or [],
                "scan_status": scan_status,
                "created_at": int(time.time()),
                "last_started": None,
                "last_exit_code": None,
                "status": "STOPPED"
            }
            data["files"][file_id] = record
            if source_type == "github":
                data["system_stats"]["total_github_deploys"] += 1
            elif source_type == "zip":
                data["system_stats"]["total_zip_deploys"] += 1
            else:
                data["system_stats"]["total_uploads"] += 1
            self._write(data)
            return record

    def update_file_entrypoint(self, file_id: str, new_rel_entry: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read()
            if file_id in data["files"]:
                rec = data["files"][file_id]
                proj_dir = rec.get("project_dir")
                if proj_dir:
                    full_new_path = os.path.join(proj_dir, new_rel_entry)
                    if os.path.exists(full_new_path):
                        project_prefix = rec["file_name"].split("/")[0]
                        rec["file_path"] = full_new_path
                        rec["file_name"] = f"{project_prefix}/{new_rel_entry}"
                        self._write(data)
                        return rec
            return None

    def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        return self._read()["files"].get(file_id)

    def get_user_files(self, user_id: int) -> List[Dict[str, Any]]:
        data = self._read()
        return [f for f in data["files"].values() if f["user_id"] == int(user_id)]

    def get_all_files(self) -> Dict[str, Any]:
        return self._read().get("files", {})

    def update_file_status(self, file_id: str, status: str, exit_code: Optional[int] = None):
        with self.lock:
            data = self._read()
            if file_id in data["files"]:
                data["files"][file_id]["status"] = status
                if status == "RUNNING":
                    data["files"][file_id]["last_started"] = int(time.time())
                if exit_code is not None:
                    data["files"][file_id]["last_exit_code"] = exit_code
                self._write(data)

    def delete_file_record(self, file_id: str):
        with self.lock:
            data = self._read()
            data["files"].pop(file_id, None)
            data["sessions"].pop(file_id, None)
            self._write(data)

    # ==========================================
    # SESSION MANAGEMENT
    # ==========================================
    def register_session(self, file_id: str, user_id: int, pid: int, log_path: str):
        with self.lock:
            data = self._read()
            data["sessions"][file_id] = {
                "file_id": file_id,
                "user_id": int(user_id),
                "pid": pid,
                "log_path": log_path,
                "started_at": int(time.time())
            }
            if file_id in data["files"]:
                data["files"][file_id]["status"] = "RUNNING"
                data["files"][file_id]["last_started"] = int(time.time())
            self._write(data)

    def remove_session(self, file_id: str):
        with self.lock:
            data = self._read()
            if file_id in data["sessions"]:
                del data["sessions"][file_id]
                self._write(data)

    def get_user_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        data = self._read()
        return [s for s in data["sessions"].values() if s["user_id"] == int(user_id)]

    def get_all_sessions(self) -> Dict[str, Any]:
        return self._read().get("sessions", {})

    def increment_stat(self, key: str):
        with self.lock:
            data = self._read()
            if key in data["system_stats"]:
                data["system_stats"][key] += 1
                self._write(data)

    def get_stats(self) -> Dict[str, Any]:
        return self._read().get("system_stats", {})


db = DatabaseManager()
