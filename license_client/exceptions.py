"""自定义异常"""


class LicenseError(Exception):
    """License 基础异常"""
    pass


class NotActivatedError(LicenseError):
    """未激活"""
    pass


class ExpiredError(LicenseError):
    """已过期"""
    pass


class HardwareMismatchError(LicenseError):
    """硬件码不匹配"""
    pass


class SignatureInvalidError(LicenseError):
    """签名验证失败"""
    pass


class ActivationFailedError(LicenseError):
    """激活失败"""
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(message or code)


class NetworkError(LicenseError):
    """网络错误"""
    pass
