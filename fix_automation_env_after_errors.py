from pathlib import Path

env_path = Path(".env")

if not env_path.exists():
    env_path.write_text("", encoding="utf-8")

lines = env_path.read_text(encoding="utf-8-sig").splitlines()

def set_env(key, value):
    global lines
    found = False
    new_lines = []

    for line in lines:
        clean = line.strip().lstrip("\ufeff")
        if clean.startswith(key + "="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line.lstrip("\ufeff"))

    if not found:
        new_lines.append(f"{key}={value}")

    lines = new_lines

# Safe automation settings
set_env("AUTOMATION_ENABLED", "true")
set_env("AUTOMATION_INTERVAL_SECONDS", "120")

# Strategy was timing out, so do it every 10 minutes not every cycle
set_env("STRATEGY_INTERVAL_SECONDS", "600")

# Backtest is heavy and --all is unsupported, so keep it manual for now
set_env("AUTOMATION_RUN_BACKTEST", "false")
set_env("BACKTEST_INTERVAL_SECONDS", "21600")

# Telegram safe fetch
set_env("TELEGRAM_FETCH_LIMIT", "30")
set_env("TELEGRAM_FETCH_LIMIT_PER_CHANNEL", "30")
set_env("TELEGRAM_DOWNLOAD_MEDIA", "false")
set_env("TELEGRAM_ANALYZE_IMAGES", "false")
set_env("TELEGRAM_SKIP_NON_IMAGE_MEDIA", "true")

# Avoid dashboard conflict
set_env("AUTO_REFRESH_ON_START", "false")

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env updated safely.")
print("AUTOMATION_RUN_BACKTEST=false")
print("STRATEGY_INTERVAL_SECONDS=600")
print("AUTOMATION_INTERVAL_SECONDS=120")
