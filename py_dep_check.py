import importlib.util as u
import sys

for m in ["pystray", "PIL", "requests", "httpx", "openai", "win32api", "pywin32"]:
    s = u.find_spec(m)
    print(f"{m:12} {'OK' if s else 'MISSING'}")

print("Python", sys.version.split()[0])
