"""codex CLI 自动安装（bootstrap.py）测试：资产映射 + 解压逻辑（无网络）。"""

from __future__ import annotations

import io
import os
import tarfile
import zipfile

import pytest

from codex_quota import bootstrap


class TestAssetName:
    @pytest.mark.parametrize("platform,machine,expected", [
        ("win32", "AMD64", "codex-x86_64-pc-windows-msvc.exe.zip"),
        ("win32", "ARM64", "codex-aarch64-pc-windows-msvc.exe.zip"),
        ("darwin", "arm64", "codex-aarch64-apple-darwin.tar.gz"),
        ("darwin", "x86_64", "codex-x86_64-apple-darwin.tar.gz"),
        ("linux", "x86_64", "codex-x86_64-unknown-linux-musl.tar.gz"),
        ("linux", "aarch64", "codex-aarch64-unknown-linux-musl.tar.gz"),
    ])
    def test_mapping(self, platform, machine, expected):
        assert bootstrap.codex_asset_name(platform, machine) == expected

    def test_unknown_arch_raises(self):
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap.codex_asset_name("linux", "riscv64")


class TestExtract:
    def test_zip_picks_exe(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.txt", "hi")
            zf.writestr("codex-x86_64-pc-windows-msvc.exe", b"MZ-fake")
        target = str(tmp_path / "bin" / "codex.exe")
        bootstrap._extract(buf.getvalue(), "https://x/codex-a.zip", target)
        with open(target, "rb") as f:
            assert f.read() == b"MZ-fake"

    def test_tarball_picks_codex_binary(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\necho hi\n"
            info = tarfile.TarInfo("./codex-x86_64-unknown-linux-musl")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        target = str(tmp_path / "bin" / "codex")
        bootstrap._extract(buf.getvalue(), "https://x/codex-a.tar.gz", target)
        with open(target, "rb") as f:
            assert f.read().startswith(b"#!/bin/sh")

    def test_empty_package_raises(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.txt", "hi")
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap._extract(buf.getvalue(), "https://x/a.zip",
                               str(tmp_path / "codex.exe"))


class TestManagedPath:
    def test_in_windows_candidates(self, monkeypatch):
        """受管安装路径必须在 Windows 候选列表里（装完即可被发现）。"""
        from codex_quota import app_server

        assert bootstrap.managed_codex_path() in app_server._windows_codex_candidates()

    def test_name_by_platform(self, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")
        assert bootstrap.managed_codex_path().endswith("codex.exe")
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        assert bootstrap.managed_codex_path().endswith("codex")
