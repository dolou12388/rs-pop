"""配置模块边界测试。

重点验证「配置坏了程序还能不能起来」和「删除被引用的供应商」。
用临时目录隔离，不碰用户真实配置。
"""

import json
import os
import shutil
import sys
import tempfile

import py_config


def disp_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - disp_len(s))


results = []


def ck(name, cond, note=""):
    results.append((name, bool(cond)))
    print("  " + pad(name, 44) + ("PASS" if cond else "FAIL")
          + (("  " + note) if note else ""))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 76)
    print("配置模块边界测试")
    print("=" * 76)

    tmp = tempfile.mkdtemp(prefix="popcfg_")
    orig = py_config.config_dir
    py_config.config_dir = lambda: tmp
    try:
        # ---------- 1 首次运行 ----------
        print("\n1 首次运行（无配置文件）")
        s = py_config.ConfigStore()
        ck("自动生成默认配置", os.path.exists(os.path.join(tmp, "config.json")))
        ck("至少一个供应商", len(s.providers()) == 1)
        ck("三动作都有绑定",
           all(s.binding(a)["provider_id"] for a in ("search", "translate", "explain")))
        ck("未配置时正确报告未就绪", not s.readiness("search")[0],
           s.readiness("search")[1])

        # ---------- 2 配置损坏 ----------
        print("\n2 配置文件被写坏")
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            f.write("{ 这不是 JSON ")
        s2 = py_config.ConfigStore()
        ck("损坏配置不崩溃且回落默认",
           len(s2.providers()) == 1 and s2.binding("search")["provider_id"])

        print("\n3 配置是合法 JSON 但结构错乱")
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"providers": "应该是数组", "bindings": 123}, f)
        s3 = py_config.ConfigStore()
        ck("结构错乱被修正", isinstance(s3.providers(), list) and len(s3.providers()) >= 1)
        ck("绑定被补全", s3.binding("translate")["provider_id"] is not None)

        print("\n4 供应商条目缺字段 / id 重复")
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "providers": [
                    {"id": "a", "base_url": "http://x", "model": "m"},
                    {"id": "a", "base_url": "http://y", "model": "n"},   # 重复 id
                    {"name": "没有 id"},                                  # 无 id
                ],
            }, f)
        s4 = py_config.ConfigStore()
        ids = [p["id"] for p in s4.providers()]
        ck("重复 id 被去重", len(ids) == len(set(ids)), f"ids={ids}")
        ck("缺 id 被补上", all(ids))
        ck("缺 timeout 有默认值", s4.providers()[0].get("timeout") == 30)

        # ---------- 5 正常配置与就绪判断 ----------
        print("\n5 就绪判断")
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "providers": [{
                    "id": "ds", "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                }],
                "bindings": {
                    "search": {"provider_id": "ds", "prompt": "p"},
                    "translate": {"provider_id": "ds", "prompt": "p"},
                    "explain": {"provider_id": "ds", "prompt": "p"},
                },
            }, f)
        s5 = py_config.ConfigStore()
        ok, why = s5.readiness("search")
        ck("有地址有模型但无 Key -> 未就绪", not ok, why)
        s5.set_key("ds", "sk-test-123")
        s5.save()
        ok, why = s5.readiness("search")
        ck("填了 Key -> 就绪", ok, why)

        # ---------- 6 密钥不进 config.json ----------
        print("\n6 密钥隔离")
        raw_cfg = open(os.path.join(tmp, "config.json"), encoding="utf-8").read()
        ck("API Key 不在 config.json 里", "sk-test-123" not in raw_cfg)
        sec = json.load(open(os.path.join(tmp, "secrets.json"), encoding="utf-8"))
        ck("API Key 在 secrets.json 里", sec.get("ds") == "sk-test-123")
        # 权限收紧是尽力而为（依赖 icacls），只做信息性检查
        import subprocess
        try:
            r = subprocess.run(["icacls", os.path.join(tmp, "secrets.json")],
                               capture_output=True, text=True, timeout=8)
            acl = r.stdout
            others = ("Everyone" in acl or "BUILTIN\\Users" in acl
                      or "*S-1-1-0" in acl)
            print(f"      权限收紧: {'仍有其他用户条目' if others else '仅当前用户'}")
            ck("密钥文件不含 Everyone/Users 条目", not others)
        except Exception as e:
            print(f"      (跳过权限检查: {e})")

        # ---------- 7 删除被引用的供应商 ----------
        print("\n7 删除被引用的供应商")
        s5.upsert_provider({"id": "bk", "name": "备用", "base_url": "http://b", "model": "m2"})
        s5.set_binding("search", provider_id="bk")
        ck("切换到备用供应商", s5.binding("search")["provider_id"] == "bk")
        ok, msg = s5.remove_provider("bk")
        ck("删除成功", ok, msg)
        ck("绑定回落到剩余供应商",
           s5.binding("search")["provider_id"] == "ds",
           f"实际={s5.binding('search')['provider_id']}")

        # ---------- 8 不能删到空 ----------
        print("\n8 保护：不能删到没有供应商")
        ok, msg = s5.remove_provider("ds")
        ck("拒绝删除最后一个", not ok, msg)
        ck("供应商仍在", len(s5.providers()) == 1)

        # ---------- 9 持久化往返 ----------
        print("\n9 持久化往返")
        s5.set_binding("translate", prompt="自定义翻译提示词")
        s5.save()
        s6 = py_config.ConfigStore()
        ck("绑定的提示词已持久化",
           s6.binding("translate")["prompt"] == "自定义翻译提示词")
        ck("Key 读回正确", s6.get_key("ds") == "sk-test-123")

        # ---------- 10 原子写入 ----------
        print("\n10 写入原子性")
        ck("无 .tmp 残留文件",
           not any(n.endswith(".tmp") for n in os.listdir(tmp)))

    finally:
        py_config.config_dir = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 76)
    fails = sum(1 for _, ok in results if not ok)
    print(f"{len(results)} 项，" + ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
