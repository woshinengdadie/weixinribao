"""License Client 主类 - 激活 + 离线验证"""
import time
import uuid
import requests
from license_client.hardware_id import get_hardware_id
from license_client.verifier import verify_token
from license_client.storage import save_license, load_license, delete_license
from license_client.exceptions import (
    LicenseError, NotActivatedError, ExpiredError,
    HardwareMismatchError, SignatureInvalidError,
    ActivationFailedError, NetworkError
)


class LicenseClient:
    """License 客户端

    使用方式：
        lc = LicenseClient(
            server_url="https://your-server-ip",
            product_id="your_app_v1",
            app_name="MyApp",
        )
        # 启动时检查
        result = lc.check()
        if not result["valid"]:
            # 弹出激活对话框，让用户输入激活码
            activate_result = lc.activate(user_input_code)
        else:
            # 已激活，进入主程序
            run_main_app()
    """

    def __init__(
        self,
        server_url: str,
        product_id: str = "your_app_v1",
        app_name: str = "MyApp",
        client_version: str = "1.0.0",
        verify_ssl: bool = False,  # IP+自签证书方案需关闭，有域名后改 True
    ):
        self.server_url = server_url.rstrip('/')
        self.product_id = product_id
        self.app_name = app_name
        self.client_version = client_version
        self.verify_ssl = verify_ssl

    def get_hardware_id(self) -> str:
        """获取本机硬件码"""
        return get_hardware_id()

    def activate(self, code: str) -> dict:
        """
        激活 License
        返回: {"ok": True, "license_id": ..., "expire_at": ...}
        或:   {"ok": False, "error": "错误码"}
        """
        hw_id = self.get_hardware_id()
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())

        try:
            resp = requests.post(
                f"{self.server_url}/api/activate",
                json={
                    "code": code.strip().upper(),
                    "hw_id": hw_id,
                    "product_id": self.product_id,
                    "nonce": nonce,
                    "timestamp": timestamp,
                    "client_version": self.client_version,
                },
                timeout=15,
                verify=self.verify_ssl,
            )
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"无法连接激活服务器: {e}")

        data = resp.json()
        if data.get("status") == "ok":
            save_license(
                license_token=data["license_token"],
                license_id=data["license_id"],
                expire_at=data["expire_at"],
                app_name=self.app_name,
            )
            return {
                "ok": True,
                "license_id": data["license_id"],
                "expire_at": data["expire_at"],
            }
        else:
            error_code = data.get("code", "UNKNOWN")
            error_map = {
                "INVALID_CODE": "激活码无效",
                "ALREADY_USED": "激活码已被使用",
                "EXPIRED": "激活码已过期",
                "HW_ALREADY_ACTIVATED": "此机器已激活过其他码",
                "RATE_LIMITED": "请求过于频繁，请稍后再试",
                "INVALID_TIMESTAMP": "时间不同步，请检查系统时间",
                "REPLAY_DETECTED": "请求重复",
            }
            raise ActivationFailedError(
                error_code,
                error_map.get(error_code, f"激活失败: {error_code}")
            )

    def check(self) -> dict:
        """
        离线验证本地 License（每次启动调用）
        返回: {"valid": True, "license_id": ..., "expire_at": ...}
        或:   {"valid": False, "reason": "原因", "error_code": "CODE"}
        """
        record = load_license(self.app_name)
        if not record:
            return {"valid": False, "reason": "未激活", "error_code": "NOT_ACTIVATED"}

        token = record.get("license_token")
        try:
            payload = verify_token(token, self.product_id)
            return {
                "valid": True,
                "license_id": payload.get("license_id"),
                "expire_at": payload.get("expire_at"),
                "expire_at_str": time.strftime(
                    "%Y-%m-%d",
                    time.localtime(payload.get("expire_at", 0))
                ),
            }
        except NotActivatedError:
            return {"valid": False, "reason": "未激活", "error_code": "NOT_ACTIVATED"}
        except SignatureInvalidError as e:
            return {"valid": False, "reason": str(e), "error_code": "SIGNATURE_INVALID"}
        except HardwareMismatchError as e:
            return {"valid": False, "reason": str(e), "error_code": "HW_MISMATCH"}
        except ExpiredError as e:
            return {"valid": False, "reason": str(e), "error_code": "EXPIRED"}
        except Exception as e:
            return {"valid": False, "reason": f"验证异常: {e}", "error_code": "UNKNOWN"}

    def verify_online(self) -> dict:
        """
        在线验证（可选，用于吊销检查）
        需要网络连接，失败时返回 {"online": False}
        """
        record = load_license(self.app_name)
        if not record or not record.get("license_id"):
            return {"online": False, "reason": "无本地License"}

        hw_id = self.get_hardware_id()
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())

        try:
            resp = requests.post(
                f"{self.server_url}/api/verify",
                json={
                    "license_id": record["license_id"],
                    "hw_id": hw_id,
                    "nonce": nonce,
                    "timestamp": timestamp,
                },
                timeout=10,
                verify=self.verify_ssl,
            )
            data = resp.json()
            return {"online": True, **data}
        except requests.exceptions.RequestException:
            return {"online": False, "reason": "网络不可用"}

    def deactivate(self):
        """删除本地 License（用于重新激活或换机）"""
        delete_license(self.app_name)
