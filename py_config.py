"""配置存储：AI 供应商与功能绑定。

设计要点（都有明确理由）：

1. 配置放用户目录，不放程序目录。
   程序可能被放到只读位置或 Program Files，写配置会失败。
   路径：%APPDATA%\\PopDict\\config.json（Windows 惯例）

2. API Key 不放在 config.json 明文里，单独存一个文件。
   两者分离，用户分享配置文件时不会连密钥一起泄漏。

3. 供应商用「列表 + 引用」而不是内联。
   用户可能配多个（OpenAI、DeepSeek、自建中转），
   搜索和翻译各自可以指向不同的那个。

4. 全部字段有默认值，配置缺失/损坏时程序仍能启动。
   这是低依赖风格的一贯做法：配置坏了应该降级，不该崩溃。

5. 只依赖标准库（urllib），不引入 requests/httpx。
   实测本机有 requests，但为零依赖目标保持一致。
"""

import json
import os
import sys
import threading

APP_DIR_NAME = "PopDict"
CONFIG_NAME = "config.json"
SECRET_NAME = "secrets.json"

# 浮层字号与内边距的可调范围。
# 下限 8：再小中文就糊成一团，失去「看得清」的意义。
# 上限 18：再大按钮会宽到比选中文字还长，浮层喧宾夺主。
PILL_FONT_MIN, PILL_FONT_MAX = 8, 18
PILL_FONT_DEFAULT = 10

# 按钮上下内边距（96 DPI 设计像素）。0 会让文字贴边，14 以上又回到
# 原来那种"上下各空一大截"的松垮感，所以给 0~14。
PILL_PAD_Y_MIN, PILL_PAD_Y_MAX = 0, 14
PILL_PAD_Y_DEFAULT = 5

# 搜索与翻译的默认提示词。故意写得简短，用户可在设置里改。
DEFAULT_PROMPTS = {
    "search": (
        "你是一个搜索问答助手。请针对用户提供的文本给出简洁、准确的解释与背景，"
        "必要时补充关键事实。使用中文回答，控制在 300 字内。"
    ),
    "translate": (
        "你是一个翻译引擎。如果输入是中文，翻译成英文；其他语言翻译成中文。"
        "只输出译文，不要解释，不要加引号。"
    ),
    "explain": (
        "解释这段文字的含义，指出其中可能存在的专业术语，用通俗中文说明。"
        "控制在 200 字内。"
    ),
}

# 内置供应商模板，方便快速填充
PRESETS = [
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "wire": "openai",
    },
    {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "wire": "openai",
    },
    {
        "name": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "wire": "openai",
    },
    {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "wire": "openai",
    },
    {
        "name": "本地 Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "wire": "openai",
        "no_key": True,
    },
    {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "wire": "openai",
    },
]


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
    return d


def config_path():
    return os.path.join(config_dir(), CONFIG_NAME)


def secret_path():
    return os.path.join(config_dir(), SECRET_NAME)


def _clamp_int(value, lo, hi, fallback):
    """把配置值夹到 [lo, hi] 内的整数；不是数字就用 fallback。

    bool 是 int 的子类，True/False 会被 int() 收成 1/0，对字号来说
    那是合法但荒谬的值，所以显式排除。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            return fallback
    return max(lo, min(hi, int(value)))


def default_config():
    """一个新装用户的配置：一个空供应商 + 搜索/翻译都指向它。"""
    return {
        "version": 1,
        "providers": [
            {
                "id": "default",
                "name": "默认供应商",
                "base_url": "",
                "model": "",
                "wire": "openai",
                "timeout": 30,
                "temperature": 0.3,
                "max_tokens": 1024,
            }
        ],
        "bindings": {
            "search": {"provider_id": "default", "prompt": DEFAULT_PROMPTS["search"]},
            "translate": {"provider_id": "default", "prompt": DEFAULT_PROMPTS["translate"]},
            "explain": {"provider_id": "default", "prompt": DEFAULT_PROMPTS["explain"]},
        },
        "ui": {
            "pill_ttl_ms": 9000,
            "min_drag_px": 4,
            "theme_mode": "system",
            # 浮层按钮的文字字号（磅）。浮层贴在光标旁，字号大一点才看得清；
            # 10 比原先写死的 9 大一档，中文墨迹高约 +3px，仍能保持紧凑。
            "pill_font_size": PILL_FONT_DEFAULT,
            # 按钮上下内边距（96 DPI 设计像素）。原来的 8 偏大，中文上下
            # 各留出一大截空白；5 让按钮高度贴近文字墨迹，观感更紧致。
            "pill_pad_y": PILL_PAD_Y_DEFAULT,
        },
    }


class ConfigStore:
    """线程安全的配置读写。托盘线程与 UI 线程都会碰它。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._cfg = None
        self._secrets = None
        self.load()

    # ---------- 读写 ----------

    def load(self):
        with self._lock:
            cfg_path = config_path()
            self._cfg = self._read_json(cfg_path, default_config())
            self._secrets = self._read_json(secret_path(), {})
            self._normalize()
            # 首次运行或文件缺失时立刻落盘，让用户能找到并编辑配置文件
            if not os.path.exists(cfg_path):
                self._write_json(cfg_path, self._cfg)
            return self._cfg

    @staticmethod
    def _read_json(path, fallback):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return fallback
            return data
        except FileNotFoundError:
            return fallback
        except (json.JSONDecodeError, OSError) as e:
            # 配置损坏不能让程序起不来
            print(f"[config] 读取 {os.path.basename(path)} 失败: {e}",
                  file=sys.stderr)
            return fallback

    def save(self):
        with self._lock:
            ok_cfg = self._write_json(config_path(), self._cfg)
            ok_sec = self._write_json(secret_path(), self._secrets)
            self._restrict_secret_perms()
            return ok_cfg and ok_sec

    @staticmethod
    def _restrict_secret_perms():
        """把 secrets.json 权限收紧到仅当前用户。

        存密钥的文件不该让同机其他账户读到。Windows 上 os.chmod 只影响
        只读位，必须用 icacls 断开继承再授予当前用户。
        失败不阻断（某些环境无 icacls 或被策略限制），但要如实提示。
        """
        path = secret_path()
        if not os.path.exists(path):
            return
        user = os.environ.get("USERNAME")
        if not user:
            return
        try:
            import subprocess
            subprocess.run(
                ["icacls", path, "/inheritance:r",
                 "/grant:r", f"{user}:F"],
                capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as e:
            print(f"[config] 收紧密钥文件权限失败（不影响功能）: {e}",
                  file=sys.stderr)

    @staticmethod
    def _write_json(path, data):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except OSError as e:
            print(f"[config] 写入 {path} 失败: {e}", file=sys.stderr)
            return False

    def _normalize(self):
        """保证结构完整，容忍用户手改坏的文件。"""
        cfg = self._cfg
        cfg.setdefault("version", 1)

        provs = cfg.get("providers")
        if not isinstance(provs, list) or not provs:
            cfg["providers"] = default_config()["providers"]
            provs = cfg["providers"]

        seen = set()
        clean = []
        for p in provs:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            if not pid or pid in seen:
                pid = f"p{len(clean) + 1}"
            seen.add(pid)
            p["id"] = pid
            p.setdefault("name", pid)
            p.setdefault("base_url", "")
            p.setdefault("model", "")
            p.setdefault("wire", "openai")
            p.setdefault("timeout", 30)
            p.setdefault("temperature", 0.3)
            p.setdefault("max_tokens", 1024)
            clean.append(p)
        cfg["providers"] = clean

        bind = cfg.get("bindings")
        if not isinstance(bind, dict):
            bind = {}
        first_id = clean[0]["id"]
        for key in ("search", "translate", "explain"):
            b = bind.get(key)
            if not isinstance(b, dict):
                b = {}
            pid = b.get("provider_id")
            if pid not in seen:
                b["provider_id"] = first_id
            b.setdefault("prompt", DEFAULT_PROMPTS.get(key, ""))
            bind[key] = b
        cfg["bindings"] = bind

        ui = cfg.get("ui")
        if not isinstance(ui, dict):
            ui = {}
        ui.setdefault("pill_ttl_ms", 9000)
        ui.setdefault("min_drag_px", 4)
        if ui.get("theme_mode") not in ("system", "light", "dark"):
            ui["theme_mode"] = "system"
        # 字号与内边距必须夹到合法区间。用户可能在设置界面之外手改了
        # config.json，越界值会让浮层变得不能用（字号 0 直接量出 0 宽，
        # 内边距 999 会把浮层撑成一整条），所以在这里兜住而不是只信任写入端。
        ui["pill_font_size"] = _clamp_int(
            ui.get("pill_font_size"), PILL_FONT_MIN, PILL_FONT_MAX,
            PILL_FONT_DEFAULT)
        ui["pill_pad_y"] = _clamp_int(
            ui.get("pill_pad_y"), PILL_PAD_Y_MIN, PILL_PAD_Y_MAX,
            PILL_PAD_Y_DEFAULT)
        cfg["ui"] = ui

    # ---------- 供应商 ----------

    def providers(self):
        with self._lock:
            return [dict(p) for p in self._cfg["providers"]]

    def provider(self, pid):
        with self._lock:
            for p in self._cfg["providers"]:
                if p["id"] == pid:
                    return dict(p)
            return None

    def upsert_provider(self, prov):
        with self._lock:
            pid = prov.get("id")
            for i, p in enumerate(self._cfg["providers"]):
                if p["id"] == pid:
                    self._cfg["providers"][i] = dict(prov)
                    return pid
            self._cfg["providers"].append(dict(prov))
            return pid

    def remove_provider(self, pid):
        """删供应商；若被绑定引用，绑定回落到第一个。"""
        with self._lock:
            provs = [p for p in self._cfg["providers"] if p["id"] != pid]
            if not provs:
                return False, "至少保留一个供应商"
            self._cfg["providers"] = provs
            fallback = provs[0]["id"]
            for key, b in self._cfg["bindings"].items():
                if b.get("provider_id") == pid:
                    b["provider_id"] = fallback
            self._secrets.pop(pid, None)
            return True, "已删除"

    # ---------- 密钥 ----------

    def get_key(self, pid):
        with self._lock:
            return self._secrets.get(pid, "")

    def set_key(self, pid, key):
        with self._lock:
            if key:
                self._secrets[pid] = key
            else:
                self._secrets.pop(pid, None)

    def has_key(self, pid):
        return bool(self.get_key(pid))

    # ---------- 绑定 ----------

    def binding(self, action):
        with self._lock:
            b = dict(self._cfg["bindings"].get(action, {}))
            p = self.provider(b.get("provider_id", ""))
            b["provider"] = p
            b["key"] = self.get_key(b.get("provider_id", ""))
            return b

    def set_binding(self, action, provider_id=None, prompt=None):
        with self._lock:
            b = self._cfg["bindings"].setdefault(action, {})
            if provider_id is not None:
                b["provider_id"] = provider_id
            if prompt is not None:
                b["prompt"] = prompt
            return b

    # ---------- 就绪检查 ----------

    def readiness(self, action):
        """返回 (ok, 原因)。供界面提示与调用前检查。"""
        b = self.binding(action)
        p = b.get("provider")
        if not p:
            return False, "未选择供应商"
        if not p.get("base_url"):
            return False, "供应商未填写接口地址"
        if not p.get("model"):
            return False, "供应商未填写模型名"
        if not b.get("key") and not p.get("no_key"):
            return False, "未填写 API Key"
        return True, "就绪"

    # ---------- 直接暴露 ----------

    @property
    def raw(self):
        with self._lock:
            return json.loads(json.dumps(self._cfg))

    def set_ui(self, key, value):
        with self._lock:
            self._cfg["ui"][key] = value


# 全局单例：UI 与托盘共用一个
_store = None
_store_lock = threading.Lock()


def store():
    global _store
    with _store_lock:
        if _store is None:
            _store = ConfigStore()
        return _store


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("配置目录:", config_dir())
    s = store()
    print("供应商数:", len(s.providers()))
    for p in s.providers():
        print(f"  {p['id']:10} {p['name']:14} model={p['model'] or '(空)'}")
    for act in ("search", "translate", "explain"):
        ok, why = s.readiness(act)
        b = s.binding(act)
        print(f"  {act:10} -> {b['provider_id']:10} {'就绪' if ok else why}")
