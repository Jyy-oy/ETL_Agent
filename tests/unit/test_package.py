from etl_agent import __version__


def test_package_version_is_defined() -> None:
    """验证应用包暴露当前版本号。"""
    assert __version__ == "0.1.0"
