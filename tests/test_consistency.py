# -*- coding: utf-8 -*-
"""一致性回归：应用名 / 版本号在各处必须完全一致（纯标准库，跨平台）。

背景：程序改过一次名（「外汇现汇买入价查询」→「中行汇率换算」），改名要
同时改到 6 个地方——config.APP_TITLE（窗口标题/托盘名/互斥量名/FindWindowW
名/配置目录名）、app.py 的品牌标题、version_info.txt 的四个字符串字段、
build.ps1 与 release.ps1 里的【码点数组】（脚本刻意用码点规避非 ASCII 编码
问题，肉眼非常容易漏改）、ci.yml 的 --name。漏一处不会报错，只会让发布物
名字/版本对不上，因此这里用测试钉住。

运行：python tests/test_consistency.py
"""
import os
import re
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 隔离 %APPDATA%：本套件会调用 config._config_dir()（它会 makedirs 并尝试
# 迁移旧目录配置），必须指向临时目录，绝不能在用户的真实配置目录里产生副作用。
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="bocfx_consistency_")

import config  # noqa: E402


def _read(name):
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as f:
        return f.read()


def _expected_codepoints(name):
    """把应用名转成 build/release 脚本里使用的码点数组字面量。"""
    return ",".join("0x%04x" % ord(ch) for ch in name)


def _version_from_info_txt():
    """从 version_info.txt 取 FileVersion（以 StringStruct 字符串为准）。"""
    txt = _read("version_info.txt")
    m = re.search(r"StringStruct\('FileVersion',\s*'([^']+)'\)", txt)
    assert m, "version_info.txt 中找不到 FileVersion"
    return m.group(1)


def test_app_title_single_source():
    """APP_TITLE 必须等于界面品牌标题，且与配置目录名一致。"""
    assert config.APP_TITLE == "中行汇率换算"
    # 配置目录名 = APP_TITLE（改名后配置目录随之一并变化）
    assert os.path.basename(config._config_dir()) == config.APP_TITLE
    app_src = _read("app.py")
    assert 'text="%s"' % config.APP_TITLE in app_src, \
        "app.py 品牌标题与 config.APP_TITLE 不一致"
    # 旧名只能作为「迁移来源」出现，不能还用在新目录/新标题上
    assert config.LEGACY_CONFIG_DIR_NAME != config.APP_TITLE


def test_codepoint_arrays_match_app_title():
    """build.ps1 / release.ps1 的码点数组必须与 APP_TITLE 精确对应。

    这是改名最容易漏的地方：脚本里写的是 `-join [char[]](0x4e2d,...)`，
    漏改不会报错，只会让打包/发布产物的文件名与程序内名字不一致。
    """
    want = _expected_codepoints(config.APP_TITLE)
    for script in ("build.ps1", "release.ps1"):
        src = _read(script)
        arrays = re.findall(r"\[char\[\]\]\(([0-9a-fx,\s]+)\)", src)
        assert arrays, "%s 中找不到码点数组" % script
        for arr in arrays:
            got = ",".join(p.strip().lower() for p in arr.split(",") if p.strip())
            assert got == want, "%s 码点数组与 APP_TITLE 不一致:\n  got=%s\n  want=%s" % (
                script, got, want)


def test_version_info_self_consistent():
    """version_info.txt 四个字符串字段与 ffi 数字版本必须自洽。"""
    txt = _read("version_info.txt")
    ver = _version_from_info_txt()
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ver), "版本号格式异常：%s" % ver
    assert "StringStruct('ProductVersion', '%s')" % ver in txt
    nums = tuple(int(x) for x in ver.split("."))
    assert "filevers=(%d, %d, %d, %d)" % nums in txt, "fixture filevers 与字符串版本不一致"
    assert "prodvers=(%d, %d, %d, %d)" % nums in txt, "prodvers 与字符串版本不一致"
    # 发布物命名相关字段
    assert "StringStruct('FileDescription', '%s')" % config.APP_TITLE in txt
    assert "StringStruct('InternalName', '%s')" % config.APP_TITLE in txt
    assert "StringStruct('ProductName', '%s')" % config.APP_TITLE in txt
    assert "StringStruct('OriginalFilename', '%s.exe')" % config.APP_TITLE in txt


def test_version_info_has_no_bom():
    """version_info.txt 不能带 BOM：它由 PyInstaller 直接解析，BOM 属隐式依赖。"""
    with open(os.path.join(BASE, "version_info.txt"), "rb") as f:
        head = f.read(3)
    assert head != b"\xef\xbb\xbf", "version_info.txt 含 UTF-8 BOM"


def test_ci_uses_same_app_name():
    """ci.yml 的 --name 必须与 APP_TITLE 一致（打包产物名同源）。"""
    src = _read(os.path.join(".github", "workflows", "ci.yml"))
    assert "--name %s" % config.APP_TITLE in src, "ci.yml 的 --name 与 APP_TITLE 不一致"
    # release job 必须有 tag ↔ version_info 的版本一致性校验步骤
    assert "FileVersion" in src, "ci.yml 缺少 tag 与 version_info 的版本校验"


def test_readme_version_example_current():
    """README 的发布示例版本必须与当前版本一致（避免文档漂移）。"""
    ver = _version_from_info_txt()
    short = ".".join(ver.split(".")[:3])
    readme = _read("README.md")
    assert "v%s" % short in readme or short in readme, \
        "README 未出现当前版本 %s" % short


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print("一致性回归 %d 项通过（应用名/版本/码点数组/CI）" % len(tests))


if __name__ == "__main__":
    main()
